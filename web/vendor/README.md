# Offline renderer dependencies

The shipped browser scripts, stylesheet, and 60 font files are byte-for-byte copies from the following npm distributions. The tarballs were checked against the SHA-512 integrity values published by the npm registry. The URLs here document provenance. The renderer loads its resources locally.

| Release tarball | Files from the tarball | Verified integrity |
| --- | --- | --- |
| [markdown-it 15.0.3](https://registry.npmjs.org/markdown-it/-/markdown-it-15.0.3.tgz) | `dist/browser/markdown-it.umd.min.js` renamed to `markdown-it/markdown-it.min.js` | `sha512-+Az7Ojz3iRGu8KYMvJ6xVhvrxP3eE+vEtJX75Ow88XBpcQNV/0lGX5ZynIIBftREo4DC2jsveAMDaeltmm8QvQ==` |
| [KaTeX 0.19.1](https://registry.npmjs.org/katex/-/katex-0.19.1.tgz) | `dist/katex.min.js`, `dist/katex.min.css`, `dist/contrib/mhchem.min.js`, `dist/fonts/*` under `katex/`, with `mhchem.min.js` beside the core script | `sha512-jG8uKntjSEcm2EpQOh0XturpPuuC9r3Sq5HLHdsFdWZT27E672SF19QEi+of0/YeOy15lDgRhKvr5614uRrEfA==` |

## Licenses and notices

- [markdown-it license](markdown-it/LICENSE). The browser build includes entities 8.1.0, linkify-it 6.1.0, mdurl 2.1.0, punycode.js 2.3.1 and uc.micro 3.0.0. Their original license texts are in [licenses/](licenses/).
- [KaTeX license](katex/LICENSE). The `hyphenate` and `escape` utilities are adapted from Facebook's React under the [Apache License 2.0](licenses/Apache-2.0.txt).
- [mhchem attribution and modification notice](licenses/mhchem-NOTICE.txt). The adaptation includes code from MathJax and Martin Hensel under the [Apache License 2.0](licenses/Apache-2.0.txt).
- [KaTeX font notices and reserved names](licenses/KaTeX-fonts-NOTICE.txt) and [SIL Open Font License 1.1](licenses/OFL-1.1.txt). Many of KaTeX's SVG images are also adapted from glyphs in KaTeX fonts. Copyright (c) 2009–2010 Design Science, Inc. and 2014–2017 Khan Academy. These glyphs use the SIL Open Font License 1.1.
