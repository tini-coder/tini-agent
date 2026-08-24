"""Entrypoints — installed as the `tini` command (and `python -m tini`):

  tini                       chat in the terminal (default)
    tini dashboard             the browser cockpit → localhost:9000 (+ Telegram if configured)
  tini connections           list configured integrations and their health
  tini voice                 talk to it (needs the [voice] extra)
  tini telegram              phone → laptop (needs TELEGRAM_BOT_TOKEN)
  tini discord               Discord → laptop (needs DISCORD_BOT_TOKEN)
  tini whatsapp              WhatsApp → laptop (needs WHATSAPP_TOKEN, public URL)
  tini brief                 morning briefing (calendar + mail + memory) — as a LOOP
  tini gather                same job as a GRAPH: github, web, calendar and
                             memory fetched together, then one digest
  tini skill install <url>   install a community skill
"""

from __future__ import annotations

import sys


def main() -> None:
    args = sys.argv[1:]
    if not args:
        from tini.gateway.cli import main as cli_main

        cli_main()
    elif args[0] == "dashboard":
        from tini.ops.dashboard import main as dash_main

        dash_main()
    elif args[0] == "connections":
        from tini.integrations import cli_main

        sys.exit(cli_main())
    elif args[0] == "voice":
        from tini.gateway.voice import main as voice_main

        voice_main()
    elif args[0] == "telegram":
        from tini.gateway.telegram import main as tg_main

        tg_main()
    elif args[0] == "discord":
        from tini.gateway.discord import main as discord_main

        discord_main()
    elif args[0] == "whatsapp":
        from tini.gateway.whatsapp import main as wa_main

        wa_main()
    elif args[0] == "brief":
        from tini.ops.brief import main as brief_main

        brief_main()
    elif args[0] == "gather":
        from tini.ops.gather import main as gather_main

        gather_main()
    elif args[0] == "skill" and len(args) >= 3 and args[1] == "install":
        from tini.memory.procedural.installer import install

        install(args[2])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
