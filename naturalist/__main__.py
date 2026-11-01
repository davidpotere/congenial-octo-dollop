"""Command line.

  python -m naturalist serve              # run everything + dashboard on :8787
  python -m naturalist check              # poll each source once and report what works
  python -m naturalist brief morning      # write a morning brief now (add --send to email/push it)
  python -m naturalist brief weekly --send
  python -m naturalist probe haikubox     # print a source's raw API response (for debugging)
  python -m naturalist demo               # dashboard filled with labeled SAMPLE data, no accounts needed
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys


def main() -> None:
    p = argparse.ArgumentParser(prog="naturalist")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8787)
    sub.add_parser("check")
    b = sub.add_parser("brief")
    b.add_argument("kind", choices=["morning", "weekly"])
    b.add_argument("--send", action="store_true")
    pr = sub.add_parser("probe")
    pr.add_argument("source", choices=["tempest", "haikubox"])
    d = sub.add_parser("demo")
    d.add_argument("--port", type=int, default=8787)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.cmd == "serve":
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)
    elif args.cmd == "demo":
        import uvicorn
        from .demo import demo_app
        uvicorn.run(demo_app(), host="0.0.0.0", port=args.port)
    else:
        asyncio.run(_oneshot(args))


async def _oneshot(args) -> None:
    from .app import Naturalist
    from .config import load_config
    nat = Naturalist(load_config())
    if args.cmd == "probe":
        if args.source == "tempest":
            await nat.tempest.discover()
            print(json.dumps(await nat.tempest._get(f"/observations/station/{nat.tempest.station_id}"), indent=2))
        else:
            from .sources.haikubox import Haikubox
            hb = Haikubox(nat.hub)
            print(json.dumps(await hb.fetch("/detections", hours=2), indent=2)[:6000])
        return
    if nat.tempest.enabled:
        try:
            await nat.tempest.discover()
        except Exception as e:
            nat.hub.source_error("tempest", e)
    await nat.poll_all()
    if args.cmd == "check":
        width = max(len(k) for k in nat.hub.status)
        for name, st in sorted(nat.hub.status.items()):
            mark = {True: "✓", False: "✗", None: "·"}[st["ok"]]
            print(f"  {mark} {name:<{width}}  {st['note']}")
        print(f"  {'✓' if nat.claude.enabled else '·'} {'claude':<{width}}  "
              f"{'ready' if nat.claude.enabled else 'add ANTHROPIC_API_KEY'}")
        return
    if args.cmd == "brief":
        fn = nat.briefs.morning if args.kind == "morning" else nat.briefs.weekly
        out = await fn(send=args.send)
        print(f"wrote {nat.briefs.site / out['page']}")


if __name__ == "__main__":
    sys.exit(main())
