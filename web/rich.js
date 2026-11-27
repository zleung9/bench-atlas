/* Offline rendering of untrusted benchmark text. Classic script, after markdown-it and KaTeX. */
(() => {
  'use strict';

  const MAX_DOCUMENT = 128_000;
  const MAX_FORMULA = 8_192;
  const safeTags = new Set('p h1 h2 h3 h4 h5 h6 blockquote ul ol li table thead tbody tr th td strong em s'.split(' '));
  const element = tag => document.createElement(tag);

  function openingAt(source, pos) {
    if (source[pos] === '$') return source[pos + 1] === '$' ? '$$' : '$';
    if (source[pos] === '\\' && (source[pos + 1] === '(' || source[pos + 1] === '[')) return source.slice(pos, pos + 2);
    return '';
  }

  // Run inside the inline tokenizer, before Markdown can consume TeX escapes or emphasis.
  // Code spans and fenced/indented code are already opaque at this layer.
  function inlineMath(state, silent) {
    const { src, pos, posMax } = state;
    const open = openingAt(src, pos);
    if (!open) {
      // Keep a stray closing delimiter visible when a record has mismatched math markers.
      if (src[pos] !== '\\' || (src[pos + 1] !== ')' && src[pos + 1] !== ']')) return false;
      if (!silent) state.pending += src.slice(pos, pos + 2);
      state.pos += 2;
      return true;
    }
    const close = open === '\\(' ? '\\)' : open === '\\[' ? '\\]' : open;
    const start = pos + open.length;
    const display = open === '$$' || open === '\\[';
    if (open === '$' && (start >= posMax || /\s/.test(src[start]))) return false;

    const limit = Math.min(posMax, start + MAX_FORMULA + close.length);
    for (let i = start; i < limit; i++) {
      if (src.startsWith(close, i) && i + close.length <= posMax) {
        // Adjacent whitespace and a following digit commonly indicate currency: $5 to $10.
        if (i === start || (open === '$' && (/\s/.test(src[i - 1]) || /[0-9]/.test(src[i + 1] || '')))) break;
        if (!silent) {
          const token = state.push('bench_math', '', 0);
          token.content = src.slice(start, i);
          token.meta = { raw: src.slice(pos, i + close.length), display };
        }
        state.pos = i + close.length;
        return true;
      }
      if (src[i] === '`' || (!display && src[i] === '\n')) break;
      if (src[i] === '\\') {
        if (open[0] === '\\' && src.startsWith(open, i)) break;
        i++; // An escaped dollar or a TeX line break cannot close the formula.
      }
    }
    // Markdown's escape rule would otherwise drop the backslash of an unmatched opener.
    if (!silent) state.pending += open;
    state.pos = start;
    return true;
  }

  // Delimiters on their own lines can enclose blank lines, arrays, and Markdown-like rows.
  function blockMath(state, startLine, endLine, silent) {
    const lineText = line => state.src.slice(state.bMarks[line] + state.tShift[line], state.eMarks[line]);
    if (state.sCount[startLine] - state.blkIndent >= 4) return false;
    const open = lineText(startLine).trimEnd();
    if (open !== '$$' && open !== '\\[') return false;
    const close = open === '$$' ? '$$' : '\\]';
    for (let line = startLine + 1; line < endLine; line++) {
      const text = lineText(line).trimEnd();
      if ((text && state.sCount[line] < state.blkIndent) || state.eMarks[line] - state.bMarks[startLine] > MAX_FORMULA || /^(?:`{3,}|~{3,})/.test(text)) break;
      if (text !== close) continue;
      if (silent) return true;
      const token = state.push('bench_math_block', '', 0);
      token.content = state.getLines(startLine + 1, line, state.blkIndent, false);
      token.meta = { raw: state.getLines(startLine, line + 1, state.blkIndent, false), display: true };
      token.map = [startLine, line + 1];
      state.line = line + 1;
      return true;
    }
    return false;
  }

  const md = window.markdownit?.({ html: false, linkify: false, typographer: false, maxNesting: 32 });
  if (md) {
    // Leave the complete spelling of links and images visible and inert.
    md.disable(['link', 'image', 'autolink', 'reference']);
    md.inline.ruler.before('escape', 'bench_math', inlineMath);
    md.block.ruler.before('fence', 'bench_math_block', blockMath, { alt: ['paragraph', 'reference', 'blockquote', 'list'] });
  }

  function mathNode(tex, raw, display, block) {
    const node = element(block ? 'div' : 'span');
    if (window.katex && tex.trim() && tex.length <= MAX_FORMULA) {
      let denied = false;
      try {
        window.katex.render(tex, node, {
          displayMode: display, output: 'htmlAndMathml', throwOnError: true,
          strict: 'ignore', maxSize: 20, maxExpand: 500,
          // Record a refused trust-sensitive command so the reader still sees the full source.
          trust: () => { denied = true; return false; },
          macros: Object.create(null)
        });
        if (!denied) return node;
      } catch { /* Unsupported or incomplete TeX remains readable, including its delimiters. */ }
    }
    node.className = 'rich-math-source';
    node.textContent = raw;
    return node;
  }

  function literalBlock(text) {
    const pre = element('pre');
    const code = element('code');
    code.textContent = text;
    pre.append(code);
    return pre;
  }

  // Build Markdown elements from a fixed vocabulary. Never parse arbitrary HTML, copy
  // token attributes, or pass Markdown output through innerHTML—even for code examples.
  function appendTokens(root, tokens) {
    const ancestors = [root];
    for (const token of tokens) {
      if (token.hidden) continue;
      const parent = ancestors[ancestors.length - 1];
      if (token.nesting && safeTags.has(token.tag)) {
        if (token.nesting === -1) { ancestors.pop(); continue; }
        const node = element(token.tag);
        if (token.tag === 'ol' && token.attrGet('start') !== null) node.start = Number(token.attrGet('start'));
        if (token.tag === 'table') {
          const wrap = element('div');
          wrap.className = 'rich-table';
          wrap.append(node);
          parent.append(wrap);
        } else parent.append(node);
        ancestors.push(node);
        continue;
      }
      switch (token.type) {
        case 'inline': {
          // MatSciBench also uses a paragraph consisting solely of a bare boxed answer.
          if (/^\\boxed\{[^\n`]*\}$/.test(token.content)) parent.append(mathNode(token.content, token.content, true, false));
          else appendTokens(parent, token.children || []);
          break;
        }
        case 'bench_math': case 'bench_math_block':
          parent.append(mathNode(token.content, token.meta.raw, token.meta.display, token.block)); break;
        case 'softbreak': case 'hardbreak': parent.append(element('br')); break;
        case 'hr': parent.append(element('hr')); break;
        case 'code_inline': {
          const code = element('code');
          code.textContent = token.content;
          parent.append(code);
          break;
        }
        case 'code_block': case 'fence': parent.append(literalBlock(token.content)); break;
        default: if (token.content) parent.append(document.createTextNode(token.content));
      }
    }
  }

  function render(value) {
    const root = element('div');
    root.className = 'bench-rich';
    if (value !== null && typeof value === 'object') {
      root.append(literalBlock(JSON.stringify(value, null, 2)));
      return root;
    }
    const text = String(value ?? '');
    if (!md || text.length > MAX_DOCUMENT) root.append(literalBlock(text));
    else appendTokens(root, md.parse(text, {}));
    return root;
  }

  window.BenchRich = Object.freeze({ render });
})();
