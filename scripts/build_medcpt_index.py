"""Build a compressed FAISS index over NCBI's precomputed MedCPT PubMed embeddings.

One-off tool, not part of the pipeline (docs/HANDOFF.md Track B, B1). It streams one chunk
(~1M papers, ~3 GB) at a time: download, add to the index, delete. Peak disk is about 9 GB; the
finished index is about 2.7 GB. Safe to stop and rerun: finished chunks are recorded in state.json.

Full build (~115 GB downloaded in total; ~6 h on a Kaggle CPU notebook):
    uv run --with faiss-cpu --with numpy python scripts/build_medcpt_index.py
Check a finished index (self-retrieval and recall vs exact search on one chunk):
    uv run --with faiss-cpu --with numpy python scripts/build_medcpt_index.py --check
Smoke test on the smallest chunk (~1.1 GB download):
    uv run --with faiss-cpu --with numpy python scripts/build_medcpt_index.py --out data/medcpt_smoke \
        --chunks 37 --train-chunks 37 --train-per-chunk 100000 --nlist 1024 --keep-downloads
    (add --check --check-chunk 37 to check it)
"""

import argparse
import http.client
import json
import os
import time
import urllib.request
from pathlib import Path

import faiss
import numpy as np

BASE = "https://ftp.ncbi.nlm.nih.gov/pub/lu/MedCPT/pubmed_embeddings/"
DIM = 768


def parse_chunks(spec: str) -> list[int]:
    """'0-37' or '5,15,25' -> list of chunk numbers."""
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += range(int(a), int(b or a) + 1)
    return out


def download(name: str, tmp: Path) -> Path:
    """Download one file into tmp, resuming a partial download. Skips files already present."""
    path = tmp / name
    if path.exists():
        return path
    part = tmp / (name + ".part")
    for attempt in range(1, 6):
        have = part.stat().st_size if part.exists() else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with urllib.request.urlopen(urllib.request.Request(BASE + name, headers=headers), timeout=60) as r:
                mode = "ab" if have and r.status == 206 else "wb"
                with open(part, mode) as f:
                    while block := r.read(8 << 20):
                        f.write(block)
            part.rename(path)
            return path
        except (OSError, http.client.HTTPException) as e:
            print(f"  download {name} failed ({e}); retry {attempt}/5", flush=True)
            time.sleep(10 * attempt)
    raise SystemExit(f"could not download {name}")


def load_chunk(c: int, tmp: Path):
    t = time.time()
    emb = download(f"embeds_chunk_{c}.npy", tmp)
    pm = download(f"pmids_chunk_{c}.json", tmp)
    x = np.load(emb, mmap_mode="r")
    ids = np.array(json.loads(pm.read_text()), dtype=np.int64)
    if x.shape != (len(ids), DIM) or x.dtype != np.float32:
        raise SystemExit(f"chunk {c}: unexpected embeddings {x.shape} {x.dtype} for {len(ids)} pmids")
    print(f"chunk {c}: {len(ids):,} papers ready ({time.time() - t:.0f}s)", flush=True)
    return x, ids, (emb, pm)


def train(args, tmp: Path):
    rng = np.random.default_rng(0)
    samples = []
    for c in parse_chunks(args.train_chunks):
        x, _, files = load_chunk(c, tmp)
        pick = np.sort(rng.choice(len(x), min(args.train_per_chunk, len(x)), replace=False))
        samples.append(np.ascontiguousarray(x[pick]))
        del x
        if not args.keep_downloads:
            for f in files:
                f.unlink()
    xt = np.concatenate(samples)
    index = faiss.index_factory(DIM, f"IVF{args.nlist},PQ{args.pq}", faiss.METRIC_INNER_PRODUCT)
    t = time.time()
    print(f"training IVF{args.nlist},PQ{args.pq} on {len(xt):,} vectors...", flush=True)
    index.train(xt)
    print(f"trained ({time.time() - t:.0f}s)", flush=True)
    return index


def save(index, state: dict, out: Path) -> None:
    faiss.write_index(index, str(out / "pubmed.ivfpq.tmp"))
    os.replace(out / "pubmed.ivfpq.tmp", out / "pubmed.ivfpq")
    (out / "state.json.tmp").write_text(json.dumps(state))
    os.replace(out / "state.json.tmp", out / "state.json")


def build(args, out: Path, tmp: Path) -> None:
    chunks = parse_chunks(args.chunks)
    if (out / "state.json").exists():
        state = json.loads((out / "state.json").read_text())
        index = faiss.read_index(str(out / "pubmed.ivfpq"))
        if index.ntotal != state["ntotal"]:
            raise SystemExit("pubmed.ivfpq and state.json disagree (crash mid-save?); delete the out dir and rebuild")
        print(f"resuming: {len(state['done'])} chunks done, {index.ntotal:,} vectors", flush=True)
    else:
        index = train(args, tmp)
        state = {"done": [], "ntotal": 0, "factory": f"IVF{args.nlist},PQ{args.pq}", "metric": "inner_product",
                 "train_chunks": args.train_chunks, "train_per_chunk": args.train_per_chunk,
                 "model": "ncbi/MedCPT-Article-Encoder", "dump": "NCBI FTP, chunks 0-36 2023-10-28, 37 2024-03-24"}
        save(index, state, out)
    for c in chunks:
        if c in state["done"]:
            continue
        t = time.time()
        x, ids, files = load_chunk(c, tmp)
        for i in range(0, len(x), 200_000):
            index.add_with_ids(np.ascontiguousarray(x[i : i + 200_000]), ids[i : i + 200_000])
        del x
        state["done"].append(c)
        state["ntotal"] = index.ntotal
        save(index, state, out)
        if not args.keep_downloads:
            for f in files:
                f.unlink()
        n_done = sum(1 for d in chunks if d in state["done"])
        print(f"chunk {c} added: {n_done}/{len(chunks)}, {index.ntotal:,} vectors ({time.time() - t:.0f}s)", flush=True)
    size = (out / "pubmed.ivfpq").stat().st_size / 1e9
    print(f"done: {out / 'pubmed.ivfpq'} ({size:.2f} GB, {index.ntotal:,} vectors)")


def check(args, out: Path, tmp: Path) -> None:
    """Article vectors stand in for queries here; the real query-encoder test comes with the pipeline."""
    index = faiss.read_index(str(out / "pubmed.ivfpq"))
    ivf = faiss.extract_index_ivf(index)
    c = int(args.check_chunk)
    x, ids, _ = load_chunk(c, tmp)
    rng = np.random.default_rng(1)
    q_rows = np.sort(rng.choice(len(x), 200, replace=False))
    q = np.ascontiguousarray(x[q_rows])

    # Recall vs exact: an exact index and an IVF-PQ index (same trained codebooks) over this one chunk.
    flat = faiss.IndexFlatIP(DIM)
    flat.add(np.ascontiguousarray(x))
    _, true_rows = flat.search(q, 10)
    true_pmids = ids[true_rows]
    sub = faiss.clone_index(index)
    sub.reset()
    for i in range(0, len(x), 200_000):
        sub.add_with_ids(np.ascontiguousarray(x[i : i + 200_000]), ids[i : i + 200_000])
    sub_ivf = faiss.extract_index_ivf(sub)

    print(f"index: {index.ntotal:,} vectors; queries: 200 article vectors from chunk {c}")
    for nprobe in (16, 64, 256):
        ivf.nprobe = sub_ivf.nprobe = nprobe
        t = time.time()
        _, full_ids = index.search(q, 10)
        ms = (time.time() - t) / len(q) * 1000
        self_hit = np.mean([ids[r] in row for r, row in zip(q_rows, full_ids)])
        _, approx = sub.search(q, 1000)
        recall = np.mean([len(set(t_) & set(a)) / 10 for t_, a in zip(true_pmids, approx)])
        print(f"nprobe={nprobe:>3}: own paper in full-index top-10 {self_hit:.1%} | "
              f"exact top-10 found in IVF-PQ top-1000 (one chunk) {recall:.1%} | {ms:.1f} ms/query")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="data/medcpt")
    p.add_argument("--tmp", help="download dir (default: <out>/downloads)")
    p.add_argument("--chunks", default="0-37")
    p.add_argument("--train-chunks", default="3,12,21,30,36", help="spread across PMID ranges (old and new papers)")
    p.add_argument("--train-per-chunk", type=int, default=400_000)
    p.add_argument("--nlist", type=int, default=32768)
    p.add_argument("--pq", type=int, default=64, help="bytes per paper")
    p.add_argument("--keep-downloads", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--check-chunk", default="18")
    args = p.parse_args()
    out = Path(args.out)
    tmp = Path(args.tmp) if args.tmp else out / "downloads"
    out.mkdir(parents=True, exist_ok=True)
    tmp.mkdir(parents=True, exist_ok=True)
    print(f"faiss {faiss.__version__}, numpy {np.__version__}", flush=True)
    check(args, out, tmp) if args.check else build(args, out, tmp)


if __name__ == "__main__":
    main()
