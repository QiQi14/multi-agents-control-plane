/* Control-plane Markdown renderer (prototype, task_193 / claude lane).
 *
 * Written because the current projection renders `.ai/project/decisions.md`
 * blockquotes as literal ">" characters and folds a nested list into one
 * paragraph. This renderer is block-recursive: a blockquote re-enters the block
 * parser, so lists, code, tables and nested quotes inside a quote all survive.
 *
 * Deliberately scoped to the Markdown this repository actually writes:
 * ATX headings, fenced code, blockquotes, ordered/unordered nested lists with
 * hard-wrapped continuations, GFM pipe tables, thematic breaks, paragraphs, and
 * the inline set (code, strong, em, strike, links, autolinks, escapes).
 *
 * Everything is HTML-escaped before it is emitted. Raw HTML in source is shown
 * as text, never injected.
 */
(function (global) {
  'use strict';

  // ---------------------------------------------------------------- escaping
  function esc(text) {
    return String(text)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // Must stay byte-identical to slugify() in tools/build_snapshot.py so the
  // generated outline anchors match the rendered heading ids.
  function slugify(text) {
    return String(text)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '');
  }

  function plain(text) {
    return String(text)
      .replace(/`([^`]*)`/g, '$1')
      .replace(/\*\*([^*]*)\*\*/g, '$1')
      .replace(/\*([^*]*)\*/g, '$1')
      .trim();
  }

  // ------------------------------------------------------------ frontmatter
  function splitFrontmatter(source) {
    // U+FEFF is never content. `.ai/memory/deprecated.md` carries a stray BOM
    // *after* its frontmatter, directly in front of `# Deprecated`, which is
    // enough to stop that line being recognised as a heading at all.
    var text = String(source == null ? '' : source)
      .replace(/\r\n?/g, '\n')
      .replace(/﻿/g, '');
    var match = /^---[ \t]*\n([\s\S]*?)\n---[ \t]*(\n|$)/.exec(text);
    if (!match) return { front: '', body: text };
    return { front: match[1], body: text.slice(match[0].length) };
  }

  // ------------------------------------------------------------------ inline
  // linkResolver(href) -> href' | null   (null keeps the original)
  function inline(text, opts) {
    var out = '';
    var i = 0;
    var n = text.length;
    var resolve = (opts && opts.linkResolver) || null;

    while (i < n) {
      var ch = text[i];

      // Backslash escape
      if (ch === '\\' && i + 1 < n && /[\\`*_{}\[\]()#+\-.!|~>]/.test(text[i + 1])) {
        out += esc(text[i + 1]);
        i += 2;
        continue;
      }

      // Code span: longest matching run of backticks
      if (ch === '`') {
        var run = 1;
        while (i + run < n && text[i + run] === '`') run++;
        var fence = text.substr(i, run);
        var close = text.indexOf(fence, i + run);
        while (close !== -1 && text[close + run] === '`') {
          close = text.indexOf(fence, close + 1);
        }
        if (close !== -1) {
          var code = text.slice(i + run, close);
          if (code.length > 1 && code[0] === ' ' && code[code.length - 1] === ' ') {
            code = code.slice(1, -1);
          }
          out += '<code>' + esc(code) + '</code>';
          i = close + run;
          continue;
        }
        out += esc(fence);
        i += run;
        continue;
      }

      // Link / image
      if (ch === '[' || (ch === '!' && text[i + 1] === '[')) {
        var isImage = ch === '!';
        var start = isImage ? i + 1 : i;
        var parsed = matchLink(text, start);
        if (parsed) {
          var href = parsed.href;
          var mapped = resolve ? resolve(href, parsed.label) : null;
          var finalHref = mapped == null ? href : mapped;
          var safe = /^(https?:|mailto:|#)/i.test(finalHref) ? finalHref : null;
          if (isImage) {
            out += '<span class="md-image-ref">' + esc(parsed.label || finalHref) + '</span>';
          } else if (safe) {
            var external = /^https?:/i.test(safe);
            out += '<a href="' + esc(safe) + '"' +
              (external ? ' rel="noreferrer noopener" data-external="1"' : '') + '>' +
              inline(parsed.label, opts) + '</a>';
          } else {
            // Unresolvable relative target: keep the text, mark the path.
            out += inline(parsed.label, opts) +
              ' <span class="md-deadlink" title="' + esc(finalHref) + '">↗</span>';
          }
          i = parsed.end;
          continue;
        }
      }

      // Bare autolink
      if (ch === '<' && /^<https?:\/\/[^>\s]+>/.test(text.slice(i))) {
        var url = /^<(https?:\/\/[^>\s]+)>/.exec(text.slice(i))[1];
        out += '<a href="' + esc(url) + '" rel="noreferrer noopener" data-external="1">' +
          esc(url) + '</a>';
        i += url.length + 2;
        continue;
      }

      // Strong / emphasis / strike
      var emphasis = matchEmphasis(text, i, opts);
      if (emphasis) {
        out += emphasis.html;
        i = emphasis.end;
        continue;
      }

      out += esc(ch);
      i++;
    }
    return out;
  }

  function matchLink(text, start) {
    if (text[start] !== '[') return null;
    var depth = 0;
    var i = start;
    for (; i < text.length; i++) {
      if (text[i] === '\\') { i++; continue; }
      if (text[i] === '[') depth++;
      else if (text[i] === ']') {
        depth--;
        if (depth === 0) break;
      }
    }
    if (depth !== 0 || text[i + 1] !== '(') return null;
    var label = text.slice(start + 1, i);
    var j = i + 2;
    var pdepth = 1;
    var target = '';
    for (; j < text.length; j++) {
      if (text[j] === '\\') { target += text[j + 1] || ''; j++; continue; }
      if (text[j] === '(') pdepth++;
      else if (text[j] === ')') {
        pdepth--;
        if (pdepth === 0) break;
      }
      if (pdepth > 0) target += text[j];
    }
    if (pdepth !== 0) return null;
    var href = target.trim().replace(/\s+"[^"]*"$/, '').trim();
    return { label: label, href: href, end: j + 1 };
  }

  var EMPHASIS = [
    { open: '***', tag: 'strong', inner: 'em' },
    { open: '___', tag: 'strong', inner: 'em' },
    { open: '**', tag: 'strong' },
    { open: '__', tag: 'strong' },
    { open: '~~', tag: 'del' },
    { open: '*', tag: 'em' }
  ];

  function matchEmphasis(text, i, opts) {
    for (var k = 0; k < EMPHASIS.length; k++) {
      var rule = EMPHASIS[k];
      var marker = rule.open;
      if (text.substr(i, marker.length) !== marker) continue;
      if (text[i + marker.length] === ' ' || text[i + marker.length] === undefined) continue;
      var close = i + marker.length;
      while (true) {
        close = text.indexOf(marker, close);
        if (close === -1) break;
        if (text[close - 1] === '\\' || text[close - 1] === ' ') {
          close += marker.length;
          continue;
        }
        break;
      }
      if (close === -1 || close === i + marker.length) continue;
      var body = text.slice(i + marker.length, close);
      if (!body.trim()) continue;
      var html = inline(body, opts);
      if (rule.inner) html = '<' + rule.inner + '>' + html + '</' + rule.inner + '>';
      return {
        html: '<' + rule.tag + '>' + html + '</' + rule.tag + '>',
        end: close + marker.length
      };
    }
    // `_word_` only when word-bounded, so snake_case identifiers survive intact.
    if (text[i] === '_' && (i === 0 || /[\s(\[{>"']/.test(text[i - 1]))) {
      var m = /^_([^_\n][^_\n]*?)_(?![A-Za-z0-9_])/.exec(text.slice(i));
      if (m) {
        return { html: '<em>' + inline(m[1], opts) + '</em>', end: i + m[0].length };
      }
    }
    return null;
  }

  // ------------------------------------------------------------------ blocks
  var RE = {
    atx: /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/,
    fence: /^ {0,3}(```+|~~~+)\s*([^`]*)$/,
    hr: /^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$/,
    quote: /^ {0,3}>[ \t]?(.*)$/,
    ul: /^(\s*)([-*+])[ \t]+(.*)$/,
    ol: /^(\s*)(\d{1,9})([.)])[ \t]+(.*)$/,
    tableSep: /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/
  };

  function isBlockStart(line) {
    return RE.atx.test(line) || RE.fence.test(line) || RE.hr.test(line) ||
      RE.quote.test(line) || RE.ul.test(line) || RE.ol.test(line);
  }

  function render(source, opts) {
    opts = opts || {};
    var split = splitFrontmatter(source);
    var lines = split.body.split('\n');
    var title = null;

    // A page shows the document title once, in its own header. When the caller
    // asks for it, the body's own leading `# Title` is lifted out and any later
    // level-1 heading is demoted, so the rendered article has exactly one H1
    // and the heading outline stays a real hierarchy.
    if (opts.liftTitle) {
      for (var i = 0; i < lines.length; i++) {
        if (!lines[i].trim()) continue;
        var lead = RE.atx.exec(lines[i]);
        if (lead && lead[1].length === 1) {
          title = plain(lead[2]);
          lines = lines.slice(0, i).concat(lines.slice(i + 1));
        }
        break;
      }
    }

    var ctx = { opts: opts, anchors: {}, headings: [], demoteH1: !!opts.liftTitle };
    var html = blocks(lines, ctx);
    return { html: html, headings: ctx.headings, frontmatter: split.front, title: title };
  }

  function anchorFor(ctx, text) {
    var base = slugify(plain(text)) || 'section';
    ctx.anchors[base] = (ctx.anchors[base] || 0) + 1;
    return ctx.anchors[base] === 1 ? base : base + '-' + (ctx.anchors[base] - 1);
  }

  function blocks(lines, ctx) {
    var out = [];
    var i = 0;
    while (i < lines.length) {
      var line = lines[i];

      if (!line.trim()) { i++; continue; }

      var fence = RE.fence.exec(line);
      if (fence) {
        var marker = fence[1][0];
        var lang = (fence[2] || '').trim();
        var body = [];
        i++;
        while (i < lines.length && !new RegExp('^ {0,3}' + marker + '{3,}\\s*$').test(lines[i])) {
          body.push(lines[i]);
          i++;
        }
        i++;
        out.push('<figure class="md-code"' + (lang ? ' data-lang="' + esc(lang) + '"' : '') +
          '><pre><code>' + esc(body.join('\n')) + '</code></pre></figure>');
        continue;
      }

      var atx = RE.atx.exec(line);
      if (atx) {
        var level = atx[1].length;
        if (level === 1 && ctx.demoteH1) level = 2;
        var text = atx[2];
        var anchor = anchorFor(ctx, text);
        ctx.headings.push({ level: level, text: plain(text), anchor: anchor });
        out.push('<h' + level + ' id="' + esc(anchor) + '" class="md-h">' +
          '<a class="md-anchor" href="#' + esc(anchor) + '" aria-hidden="true">#</a>' +
          inline(text, ctx.opts) + '</h' + level + '>');
        i++;
        continue;
      }

      if (RE.hr.test(line)) {
        out.push('<hr class="md-rule">');
        i++;
        continue;
      }

      if (RE.quote.test(line)) {
        var quoted = [];
        while (i < lines.length) {
          var q = RE.quote.exec(lines[i]);
          if (q) {
            quoted.push(q[1]);
            i++;
            continue;
          }
          // Lazy continuation: plain text directly under a quote line stays in
          // the quote (CommonMark paragraph continuation).
          if (lines[i].trim() && !isBlockStart(lines[i]) && quoted.length &&
              quoted[quoted.length - 1].trim()) {
            quoted.push(lines[i]);
            i++;
            continue;
          }
          break;
        }
        out.push('<blockquote class="md-quote">' + blocks(quoted, ctx) + '</blockquote>');
        continue;
      }

      if (RE.ul.test(line) || RE.ol.test(line)) {
        var list = parseList(lines, i, ctx);
        out.push(list.html);
        i = list.next;
        continue;
      }

      if (line.indexOf('|') !== -1 && i + 1 < lines.length && RE.tableSep.test(lines[i + 1])) {
        var table = parseTable(lines, i, ctx);
        if (table) {
          out.push(table.html);
          i = table.next;
          continue;
        }
      }

      // Paragraph: fold hard-wrapped lines into one flow. Two trailing spaces
      // or a backslash keep an explicit line break.
      //
      // The fold happens BEFORE inline parsing, never after: the corpus wraps
      // at ~100 columns straight through `**bold**` spans (decisions.md line 22
      // opens `**federated` and closes `graph domains**` on the next line).
      // Inlining line by line and joining the HTML leaves those markers
      // literal, so each hard-break-delimited segment is joined to raw text
      // first and parsed once.
      var segments = [];
      var current = [];
      while (i < lines.length && lines[i].trim() && !isBlockStart(lines[i])) {
        if (lines[i].indexOf('|') !== -1 && i + 1 < lines.length && RE.tableSep.test(lines[i + 1])) break;
        var raw = lines[i];
        current.push(raw.trim().replace(/\\$/, ''));
        if (/( {2,}|\\)$/.test(raw)) {
          segments.push(current.join(' '));
          current = [];
        }
        i++;
      }
      if (current.length) segments.push(current.join(' '));
      if (segments.length) {
        out.push('<p>' + segments.map(function (segment) {
          return inline(segment, ctx.opts);
        }).join('<br>') + '</p>');
      }
    }
    return out.join('\n');
  }

  function listMatch(line) {
    var ul = RE.ul.exec(line);
    if (ul) {
      return { indent: ul[1].length, ordered: false, marker: ul[2], text: ul[3], width: ul[1].length + ul[2].length + 1 };
    }
    var ol = RE.ol.exec(line);
    if (ol) {
      return {
        indent: ol[1].length, ordered: true, marker: ol[2], start: parseInt(ol[2], 10),
        text: ol[4], width: ol[1].length + ol[2].length + ol[3].length + 1
      };
    }
    return null;
  }

  function parseList(lines, start, ctx) {
    var first = listMatch(lines[start]);
    var baseIndent = first.indent;
    var ordered = first.ordered;
    var items = [];
    var i = start;
    var loose = false;

    while (i < lines.length) {
      var here = listMatch(lines[i]);
      if (!here || here.indent < baseIndent) break;
      if (here.indent > baseIndent + 1 && items.length === 0) break;
      if (here.indent >= baseIndent + 2 && items.length) {
        // Belongs to the previous item's nested content; handled below.
      }
      if (here.indent > baseIndent + 1) break;
      if (here.ordered !== ordered) break;

      var content = [here.text];
      var contentIndent = here.width;
      i++;
      var sawBlank = false;
      while (i < lines.length) {
        var line = lines[i];
        if (!line.trim()) {
          // A blank line ends the item only if the next line is not still
          // inside it.
          var lookahead = i + 1;
          while (lookahead < lines.length && !lines[lookahead].trim()) lookahead++;
          if (lookahead >= lines.length) break;
          var nextIndent = lines[lookahead].length - lines[lookahead].replace(/^\s+/, '').length;
          var nextItem = listMatch(lines[lookahead]);
          if (nextIndent >= contentIndent || (nextItem && nextItem.indent > baseIndent)) {
            content.push('');
            sawBlank = true;
            loose = true;
            i++;
            continue;
          }
          break;
        }
        var indent = line.length - line.replace(/^\s+/, '').length;
        var asItem = listMatch(line);
        if (asItem && asItem.indent <= baseIndent + 1) break;
        if (indent >= contentIndent - 1 || (asItem && asItem.indent > baseIndent)) {
          content.push(line.slice(Math.min(indent, contentIndent)));
          i++;
          continue;
        }
        // Lazy continuation of a hard-wrapped item line.
        if (!sawBlank && !isBlockStart(line)) {
          content.push(line.trim());
          i++;
          continue;
        }
        break;
      }
      items.push(content);
    }

    var html = items.map(function (content) {
      var inner = blocks(content, ctx);
      if (!loose) {
        // Tight item: unwrap a single leading paragraph so list rows stay dense.
        inner = inner.replace(/^<p>([\s\S]*?)<\/p>/, '$1');
      }
      return '<li>' + inner + '</li>';
    }).join('\n');

    var tag = ordered ? 'ol' : 'ul';
    var attrs = ' class="md-list' + (loose ? ' md-list-loose' : '') + '"';
    if (ordered && first.start !== 1) attrs += ' start="' + first.start + '"';
    return { html: '<' + tag + attrs + '>' + html + '</' + tag + '>', next: i };
  }

  function splitRow(line) {
    var trimmed = line.trim().replace(/^\|/, '').replace(/\|$/, '');
    var cells = [];
    var current = '';
    for (var i = 0; i < trimmed.length; i++) {
      if (trimmed[i] === '\\' && trimmed[i + 1] === '|') { current += '|'; i++; continue; }
      if (trimmed[i] === '|') { cells.push(current); current = ''; continue; }
      current += trimmed[i];
    }
    cells.push(current);
    return cells.map(function (c) { return c.trim(); });
  }

  function parseTable(lines, start, ctx) {
    var header = splitRow(lines[start]);
    var aligns = splitRow(lines[start + 1]).map(function (cell) {
      if (/^:.*:$/.test(cell)) return 'center';
      if (/:$/.test(cell)) return 'right';
      if (/^:/.test(cell)) return 'left';
      return '';
    });
    if (header.length < 2) return null;
    var rows = [];
    var i = start + 2;
    while (i < lines.length && lines[i].trim() && lines[i].indexOf('|') !== -1) {
      rows.push(splitRow(lines[i]));
      i++;
    }
    function cell(tag, text, index) {
      var align = aligns[index] ? ' style="text-align:' + aligns[index] + '"' : '';
      return '<' + tag + align + '>' + inline(text, ctx.opts) + '</' + tag + '>';
    }
    var html = '<div class="md-table-wrap"><table class="md-table"><thead><tr>' +
      header.map(function (h, x) { return cell('th', h, x); }).join('') +
      '</tr></thead><tbody>' +
      rows.map(function (row) {
        return '<tr>' + header.map(function (_, x) {
          return cell('td', row[x] === undefined ? '' : row[x], x);
        }).join('') + '</tr>';
      }).join('') +
      '</tbody></table></div>';
    return { html: html, next: i };
  }

  global.CPMarkdown = {
    render: render,
    inline: function (text, opts) { return inline(String(text || ''), opts || {}); },
    escape: esc,
    slugify: slugify,
    splitFrontmatter: splitFrontmatter
  };
})(window);
