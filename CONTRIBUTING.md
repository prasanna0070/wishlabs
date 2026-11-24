# Contributing to Wish Labs

Thanks for your interest. Issues and pull requests are welcome.

## Set up

Follow **Run it yourself** in the [README](README.md). The studio and server run locally with no cloud account
(`AGENT=scripted`, built-in stand-in builder). Real builds need the toolchain captured in
[`studio/builder/Dockerfile`](studio/builder/Dockerfile): CadQuery 2.8, KiCad 9, Freerouting 2.5, PlatformIO and
PrusaSlicer 2.7.

## Tests

```bash
cd studio/engine && pytest            # engine: run the full suite inside the builder image (it needs the toolchain)
cd studio/web/server && pytest        # API server
cd studio/web/app && npm test         # studio (vitest)
```

## Rules the code already follows

- **Provenance or silence.** Generated text (guides, notes, captions) never invents a dimension, pinout, price or
  test result. Anything unverified is labelled: "estimate", "not modeled", "physical checks still needed".
- **The catalog is the boundary.** A design may only use parts from `studio/library/`. Adding a part means adding
  its data, its sources and its checks there first.
- **A failed check is shown, never hidden.** If you add a check, give it a stable id and a plain-English message.
- Python 3.12 for the engine and server, TypeScript for the studio. Small pure functions and type hints.

## Vendor evidence

The engine can ship the saved vendor listings and datasheets behind each price and dimension. Those third-party
pages are not redistributed in this repository; `studio/library/tools/fetch_listings.py` saves your own copies with
their sha256 into `studio/library/references/`.

## License

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
