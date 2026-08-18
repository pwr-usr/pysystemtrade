# XTQuant documentation archive: `250807.1.2`

This directory is a pinned research corpus for evaluating XTQuant as a Chinese-futures market-data and execution adapter. It is not an endorsement, a support contract, or proof that a feature is enabled by a particular broker or QMT edition.

The archive was assembled on 2026-08-09 from official Xuntou/ThinkTrader sources. The pinned distribution is the public PyPI wheel `xtquant 250807.1.2`, whose SHA-256 is:

```text
91f19ff9a92971c5abe64fbd077e5212e0418f0820aa3427aef3444230f72921
```

The wheel itself, native binaries, and full Python wrapper source are deliberately not committed. See [`manifest.json`](manifest.json) for byte lengths, source URLs, original and archived hashes, transformation metadata, and exclusions. See [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) before redistributing any upstream material.

## Reading order and authority

1. [`package-docs/xtdata.md`](package-docs/xtdata.md) and [`package-docs/xttrader.md`](package-docs/xttrader.md) are byte-preserved manuals extracted from the pinned wheel. They are the release-closest official documentation in this corpus.
2. [`generated/api-doc-coverage.md`](generated/api-doc-coverage.md) and the source-derived indexes under [`generated/`](generated/) statically describe the pinned wrapper source. They expose manual omissions and signature drift, but they are generated evidence rather than vendor documentation or a runtime support guarantee.
3. [`native-api/`](native-api/) contains normalized snapshots of the complete six-page XTQuant Native API sidebar plus the official Linux XTData guide. Each page carries its own staleness or scope warning. The web XTData and XTTrader manuals are older than the bundled manuals.
4. [`data/`](data/) contains the supporting entitlement, futures-data, and simulation-account pages needed to interpret whether a futures workflow is actually available.
5. [`github-241014/`](github-241014/) is the byte-preserved, older official GitHub documentation snapshot at commit `5d5cfbd26972e5efe7f53759c3efcf9743730e42`. It is historical context only and is superseded by the pinned package manuals.

Even the bundled manuals are incomplete relative to the current wrapper source. When a manual, generated signature index, broker terminal, and observed runtime disagree, do not infer behavior: validate against the exact broker/QMT build in a paper account before live use.

## Corpus boundary

For this archive, “all XTQuant documentation” means the following finite, reproducible set:

- Both Markdown manuals bundled in `xtquant 250807.1.2`.
- Every page exposed by the official XTQuant Native API sidebar at retrieval time:
  - [quick start](native-api/start-now.md)
  - [XTData web manual](native-api/xtdata.md)
  - [XTTrader web manual](native-api/xttrader.md)
  - [complete examples](native-api/code-examples.md)
  - [FAQ](native-api/faq.md)
  - [download history](native-api/downloads.md)
- The official [Linux XTData guide](native-api/linux-guide.md), which is outside that six-page sidebar but materially constrains deployment.
- The supporting [edition and entitlement page](data/access-and-entitlements.md), [futures data dictionary](data/futures.md), and broad QMT [interface-operation page containing simulation-account setup](data/simulation-account-setup.md).
- The three Markdown documents in the official GitHub `version_241014/doc/` snapshot.
- Source-derived signature, constant, type, and manual-coverage indexes generated from a statically parsed copy of the pinned wrapper modules.

This is not a mirror of the complete QMT knowledge base, the built-in Python API, the VBA API, the wider ThinkTrader help center, or commercial algorithm-product documentation. In particular, the separate smart-algorithm help page is securities-oriented, lies outside the defined XTQuant Native API corpus, and is not included. External image assets are not vendored; normalized Markdown uses absolute official URLs for them.

## Source inventory

### Pinned package manuals

| Local file | Source inside wheel | Upstream state |
|---|---|---|
| [`package-docs/xtdata.md`](package-docs/xtdata.md) | `xtquant/doc/xtdata.md` | Change history through 2024-10-16 |
| [`package-docs/xttrader.md`](package-docs/xttrader.md) | `xtquant/doc/xttrader.md` | Change history through 2025-06-03; its introductory Python-version statement is older than wheel metadata |

`package-docs/xttrader.md` intentionally retains CRLF line endings and no final newline. Do not run formatters over `package-docs/` or `github-241014/`; their hashes are provenance evidence.

### Normalized Native API pages

| Local file | Official source | Important qualification |
|---|---|---|
| [`native-api/start-now.md`](native-api/start-now.md) | `nativeApi/start_now.html` | Lists Python only through 3.12; wheel metadata includes 3.13 |
| [`native-api/xtdata.md`](native-api/xtdata.md) | `nativeApi/xtdata.html` | Change history ends 2024-05-27 |
| [`native-api/xttrader.md`](native-api/xttrader.md) | `nativeApi/xttrader.html` | Change history ends 2024-06-27 |
| [`native-api/code-examples.md`](native-api/code-examples.md) | `nativeApi/code_examples.html` | Predominantly securities-oriented; not an end-to-end futures guide |
| [`native-api/faq.md`](native-api/faq.md) | `nativeApi/question_function.html` | Contains an older Python 3.6–3.11 statement |
| [`native-api/downloads.md`](native-api/downloads.md) | `nativeApi/download_xtquant.html` | Release-family IDs, dates, and PyPI versions are not one consistent version sequence |
| [`native-api/linux-guide.md`](native-api/linux-guide.md) | official Linux quick-start URL | XTData only; the page explicitly says XTTrade is unsupported on Linux |

### Supporting pages

| Local file | Why it is in scope |
|---|---|
| [`data/access-and-entitlements.md`](data/access-and-entitlements.md) | Edition, simulation, futures-data, quota, and direct-futures availability |
| [`data/futures.md`](data/futures.md) | Official futures exchanges, symbols, contract metadata, and data products |
| [`data/simulation-account-setup.md`](data/simulation-account-setup.md) | Contains the official futures simulation-account setup section; the rest is broader QMT UI documentation |

## Version and platform caveats

- PyPI identifies the pinned build as `250807.1.2`, uploaded 2026-07-21. The official download history labels the related public release family `xtquant_250807` in a row dated 2025-12-19. These are recorded as separate upstream facts, not reconciled into a fabricated release date.
- Wheel metadata advertises Windows, CPython, and Python 3.6 through 3.13. The artifact contains Windows `.pyd` and DLL binaries despite its `py3-none-any` filename.
- Trading requires a running QMT/MiniQMT client and an eligible broker/account/edition. The official Linux page supports XTData only, not XTTrade.
- Market data, direct-futures access, simulation, depth, quotas, and function-order permission are entitlement-dependent. Public package presence does not establish account access.

## Normalization method

The server-rendered HTML for each official page was retained temporarily only long enough to hash and transform it. The committed Markdown was produced by:

1. selecting the VuePress `div.theme-default-content` article body;
2. removing navigation, footer/UI chrome, scripts, comments, copy controls, line-number scaffolding, and syntax-color spans;
3. retaining article headings, prose, tables, callouts, lists, examples, and code;
4. converting callouts to blockquotes and code to fenced blocks with language labels;
5. resolving article links and image sources to absolute official URLs; and
6. converting the cleaned HTML with Pandoc `3.9.0.2` to GitHub-flavored Markdown without line wrapping.

The raw HTML is not committed, but its SHA-256 and byte length are in `manifest.json`. Archive provenance banners are additions and are included in each normalized file's archived hash.

## Verification

From this directory, verify every archived file except the checksum list itself with:

```shell
shasum -a 256 -c checksums.sha256
```

The manifest is the source of provenance metadata; `checksums.sha256` is the integrity list for the committed archive.
