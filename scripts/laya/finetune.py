"""Fine-tune the decision model (Laya) on Nexus's own decisions, on this Mac.

Adapted from Laya's Apple Silicon fine-tuning script
(github.com/NandhaKishorM/laya, notebooks/laya_finetune_typed_decisions_mps.py,
Apache-2.0): the same RLCD training objective, optimiser schedule,
temperature calibration and checkpoint layout. What changes is the data --
data/laya/train.jsonl and val.jsonl from export.py instead of the public
typed-decisions set -- and the starting point, the typed-decisions checkpoint
the runtime already serves.

Runs in its own environment so torch never enters the runtime:

    uv venv -p 3.12 data/laya/.venv
    uv pip install -p data/laya/.venv/bin/python laya==0.3.26 torch transformers safetensors huggingface_hub
    data/laya/.venv/bin/python scripts/laya/finetune.py --epochs 3

Writes data/laya/finetuned/, a checkpoint the runtime loads with
RUNTIME_DECIDE_MODEL=<that path> and RUNTIME_DECIDE_SUBFOLDER= (empty).
Stop the runtime first: training wants the GPU memory the chat model holds.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
from pathlib import Path

import torch
from huggingface_hub import snapshot_download
from safetensors.torch import load_file, save_file
from transformers import AutoTokenizer

from laya.agent import _fix_tokenizer_config
from laya.common import QTYPES, build_model, build_sequence, proper_reward, render_options

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "laya"


def base_checkpoint(repo: str, subfolder: str) -> Path:
    """A writable copy of the starting checkpoint (the HF cache is left alone)."""
    target = DATA / "base"
    if not (target / "model.safetensors").exists():
        snap = Path(snapshot_download(repo, allow_patterns=[f"{subfolder}/*"], local_files_only=True))
        shutil.copytree(snap / subfolder, target, dirs_exist_ok=True, symlinks=False)
        _fix_tokenizer_config(str(target))
    return target


def to_items(rows: list[dict], tokenizer, cfg) -> list[dict]:
    items = []
    for row in rows:
        state, questions, gold = (json.loads(row[k]) for k in ("state", "questions", "gold"))
        for qid, q in questions.items():
            if qid not in gold:
                continue
            criteria = q.get("criteria", {})
            if q["type"] == "choice":
                target = [gold[qid]["probabilities"].get(k, 0.0) for k in criteria]
            elif q["type"] == "noul":
                p = gold[qid]["probabilities"]
                target = [p.get("false", 0.5), p.get("true", 0.5)]
            else:
                continue
            total = sum(target)
            target = [t / total for t in target] if total else [1 / len(target)] * len(target)
            sequence, markers = build_sequence(
                tokenizer, state, {"t": q["type"], "ins": q["instructions"], "crit": criteria},
                cfg["max_len"], cfg["head_max_len"],
            )
            if len(markers) != len(render_options({"t": q["type"], "crit": criteria})):
                continue
            items.append({"ids": sequence, "markers": markers, "qtype": QTYPES[q["type"]],
                          "target": target, "label": target.index(max(target))})
    return items


def collate(items, pad_id):
    n, seq = len(items), max(len(i["ids"]) for i in items)
    kmax = max(len(i["markers"]) for i in items)
    ids = torch.full((n, seq), pad_id, dtype=torch.long)
    att = torch.zeros((n, seq), dtype=torch.long)
    pos = torch.zeros((n, kmax), dtype=torch.long)
    mask = torch.zeros((n, kmax), dtype=torch.bool)
    target = torch.zeros((n, kmax), dtype=torch.float32)
    for i, item in enumerate(items):
        ids[i, :len(item["ids"])] = torch.tensor(item["ids"])
        att[i, :len(item["ids"])] = 1
        k = len(item["markers"])
        pos[i, :k] = torch.tensor(item["markers"])
        mask[i, :k] = True
        target[i, :len(item["target"])] = torch.tensor(item["target"])
    return ids, att, pos, mask, target, torch.tensor([i["qtype"] for i in items])


def fit_temperature(samples) -> float:
    if len(samples) < 10:
        return 1.0
    kmax = max(len(lg) for lg, _ in samples)
    logits = torch.full((len(samples), kmax), -1e4)
    targets = torch.zeros((len(samples), kmax))
    for i, (lg, tg) in enumerate(samples):
        logits[i, :len(lg)] = torch.as_tensor(lg)
        targets[i, :len(tg)] = torch.as_tensor(tg, dtype=torch.float32)
    log_t = torch.zeros(1, requires_grad=True)
    opt = torch.optim.LBFGS([log_t], lr=0.1, max_iter=100)

    def closure():
        opt.zero_grad()
        loss = -(targets * torch.log_softmax(logits / log_t.exp(), -1)).sum(-1).mean()
        loss.backward()
        return loss

    opt.step(closure)
    return float(torch.clamp(log_t.exp(), 0.1, 10.0).item())


def save(model, tokenizer, cfg, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    save_file({k: v.detach().half().cpu().contiguous() for k, v in model.state_dict().items()},
              str(path / "model.safetensors"))
    model.encoder.config.save_pretrained(path / "encoder")
    tokenizer.save_pretrained(path / "tokenizer")
    (path / "rl_agent_config.json").write_text(json.dumps(cfg, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", default="convaiinnovations/laya")
    parser.add_argument("--subfolder", default="typed-decisions")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--micro-batch", type=int, default=4)
    parser.add_argument("--grad-accum", type=int, default=8)
    parser.add_argument("--device", choices=["mps", "cpu"], default="mps")
    args = parser.parse_args()

    device = torch.device(args.device if args.device == "cpu" or torch.backends.mps.is_available() else "cpu")
    base = base_checkpoint(args.repo, args.subfolder)
    cfg = json.loads((base / "rl_agent_config.json").read_text())
    cfg.setdefault("max_len", 1024)
    cfg.setdefault("head_max_len", 256)
    tokenizer = AutoTokenizer.from_pretrained(base / "tokenizer")

    read = lambda name: [json.loads(l) for l in (DATA / name).read_text().splitlines() if l.strip()]
    train_items = to_items(read("train.jsonl"), tokenizer, cfg)
    calib_items = to_items(read("val.jsonl"), tokenizer, cfg)
    print(f"device={device} train_items={len(train_items)} calib_items={len(calib_items)}")

    model = build_model(cfg, encoder_dir=base / "encoder")
    model.load_state_dict(load_file(str(base / "model.safetensors")), strict=True)
    model.float()
    model.encoder.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model.head_checkpointing = True
    model.to(device).train()

    enc = [p for n, p in model.named_parameters() if "encoder." in n]
    head = [p for n, p in model.named_parameters() if "encoder." not in n]
    optimizer = torch.optim.AdamW([{"params": enc, "lr": 2.5e-5}, {"params": head, "lr": 1e-4}],
                                  weight_decay=0.01)
    updates = max(1, math.ceil(len(train_items) / args.micro_batch / args.grad_accum) * args.epochs)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=updates, eta_min=1e-6)

    for epoch in range(args.epochs):
        random.Random(42 + epoch).shuffle(train_items)
        optimizer.zero_grad(set_to_none=True)
        sigma = 0.4 + (0.1 - 0.4) * epoch / max(1, args.epochs - 1)
        total, n = 0.0, 0
        for start in range(0, len(train_items), args.micro_batch):
            ids, att, pos, mask, target, qtype = (t.to(device) for t in collate(
                train_items[start:start + args.micro_batch], tokenizer.pad_token_id))
            logits, act = model(ids, att, pos, mask, qtype)
            logits = logits.float()
            k = mask.sum(-1, keepdim=True).float()
            eps = torch.randn((4,) + logits.shape, device=device) * sigma * mask
            eps = (eps - eps.sum(-1, keepdim=True) / k) * mask
            noisy = logits.detach().unsqueeze(0) + eps
            probs = torch.softmax(noisy.masked_fill(~mask, -1e4), -1)
            with torch.no_grad():
                reward = proper_reward(probs, target.unsqueeze(0), qtype, mask, w_sph=0.75, w_rps=1.0)
                adv = (reward - reward.mean(0, keepdim=True))
                adv = adv / (adv.std() + 1e-6)
            logp = -(((noisy - logits.unsqueeze(0)) ** 2) * mask).sum(-1) / (2 * sigma ** 2)
            loss_rl = -(adv * logp).mean()
            loss_ce = -(target * torch.log_softmax(logits.masked_fill(~mask, -1e4), -1)).sum(-1).mean()
            loss = (loss_rl + loss_ce + 0.0 * act.sum()) / args.grad_accum
            loss.backward()
            n += 1
            if n % args.grad_accum == 0 or start + args.micro_batch >= len(train_items):
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)
            total += loss.item() * args.grad_accum
            if n % 50 == 0:
                print(f"epoch {epoch + 1} step {n} loss {loss.item() * args.grad_accum:.4f}", flush=True)
        print(f"epoch {epoch + 1}/{args.epochs} avg loss {total / max(1, n):.4f}", flush=True)

    model.eval()
    samples = [[] for _ in range(3)]
    with torch.no_grad():
        for start in range(0, len(calib_items), args.micro_batch):
            chunk = calib_items[start:start + args.micro_batch]
            ids, att, pos, mask, target, qtype = (t.to(device) for t in collate(chunk, tokenizer.pad_token_id))
            logits, _ = model(ids, att, pos, mask, qtype)
            for i, item in enumerate(chunk):
                samples[item["qtype"]].append((logits[i, :len(item["markers"])].cpu(), item["target"]))
    temperatures = [fit_temperature(s) if s else 1.2 for s in samples]
    cfg.update({"fine_tuned": True, "model_name": "laya-nexus-decisions", "temperature": temperatures})
    cfg.pop("temperature_by_options", None)
    out = DATA / "finetuned"
    save(model, tokenizer, cfg, out)
    print(f"saved {out}; temperatures {temperatures}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
