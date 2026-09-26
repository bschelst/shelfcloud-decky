# Developer notes

Hard-won details about this plugin that are not obvious from the code, and in
some cases not obvious from Decky's or Steam's documentation either.

| Document | Read it when |
|---|---|
| [RPC-PAYLOAD-LIMITS.md](RPC-PAYLOAD-LIMITS.md) | **Before adding or changing any RPC.** A single message over 4 MB closes Decky's websocket silently and freezes the plugin with an empty log. Cost multiple days, twice. |
| [CONTROLLER-LAYOUTS.md](CONTROLLER-LAYOUTS.md) | Working on Steam Input layouts, VDFs, or per-provider controller configs. |
| [bugs/](bugs/) | Known open issues with what has been verified on hardware. |
