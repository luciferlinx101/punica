import itertools
import json
import pathlib
from datetime import datetime

import pytz
import torch
from tqdm import tqdm

import punica.ops
from punica.utils.kvcache import BatchedKvCache, KvCache, KvPool

from .benchmark_utils import bench, gc_torch


class batch_decode_Resources:

  def __init__(
      self,
      num_heads: int,
      head_dim: int,
      block_len: int,
      seqlens: list[int],
      dtype: str,
      device: torch.device,
  ):
    dtype = getattr(torch, dtype)
    self.kvpool = KvPool(
        num_layers=1,
        num_heads=num_heads,
        head_dim=head_dim,
        capacity=sum((l + block_len - 1) // block_len for l in seqlens),
        block_len=block_len,
        dtype=dtype,
        device=device,
    )
    self.q = torch.randn((len(seqlens), num_heads, head_dim),
                         dtype=dtype,
                         device=device)
    kv_list: list[KvCache] = []
    for seqlen in seqlens:
      kv_list.append(KvCache(self.kvpool, seqlen))
    self.kv_list = kv_list
    self.kv = BatchedKvCache(kv_list)

  def release(self):
    for kvcache in self.kv_list:
      kvcache.release()


def bench_batch_decode(f):
  num_heads_ = [32, 40]
  batch_size_ = [
      1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 16, 20, 24, 28, 32, 40, 48, 56, 64
  ]
  seqlen_ = list(reversed(range(2048, 0, -64)))
  dtype = "float16"
  device = torch.device("cuda:0")
  block_len = 16
  head_dim = 128

  all_ = list(itertools.product(num_heads_, seqlen_, batch_size_))
  for num_heads, seqlen, batch_size in (pbar := tqdm(all_)):
    setup = dict(
        num_heads=num_heads,
        head_dim=head_dim,
        block_len=block_len,
        seqlen=seqlen,
        batch_size=batch_size,
    )
    pbar.set_postfix(setup)
    torch.manual_seed(0xabcdabcd987)
    gc_torch()
    try:
      res = batch_decode_Resources(
          num_heads=num_heads,
          head_dim=head_dim,
          block_len=block_len,
          seqlens=[seqlen] * batch_size,
          dtype=dtype,
          device=device,
      )
    except torch.cuda.OutOfMemoryError:
      print("OOM", setup)
      continue

    try:
      result = bench(
          lambda: punica.ops.mha_rope_decode(res.q, res.kv, layer_idx=0))
    except torch.cuda.OutOfMemoryError:
      res.release()
      print("OOM", setup)
      continue

    res.release()

    result = {"setup": setup, "avg": result.avg(), "std": result.std()}
    f.write(json.dumps(result) + "\n")
    f.flush()


def main():
  this_file = pathlib.Path(__file__)
  project_root = this_file.parents[1]
  now = datetime.now(pytz.timezone("US/Pacific"))
  out_filename = f"{now:%Y%m%d-%H%M%S}-{this_file.stem}.jsonl"
  out_path = project_root / "data" / out_filename

  print(out_path)
  with open(out_path, "w") as f:
    bench_batch_decode(f)


if __name__ == "__main__":
  main()
