/* Control-plane reader prototype — task_193 / synthesis lane.
 *
 * Screens: project intelligence, home, document catalog, document reader, knowledge graph with an
 * inline reader, task catalog, task detail. All state that a reader would want
 * to share or step back through lives in the URL hash:
 *
 *   #/home
 *   #/docs?q=…&group=…&type=…&status=…&sort=…
 *   #/doc/<id>?view=read|source#<anchor>
 *   #/graph?sel=<id>&prov=…&type=…
 *   #/tasks?q=…&life=…&feature=…&risk=…&tool=…&sort=…
 *   #/task/<id>?view=spec|source
 *
 * No framework, no network, no build step at read time.
 */
(function () {
  'use strict';

  var DATA = window.CP_DATA;
  var PROJECT = window.CONTROL_PLANE_PROJECT;
  var MD = window.CPMarkdown;
  var main = document.getElementById('main');

  // ------------------------------------------------------------------ index
  var docById = {};
  var docByPath = {};
  DATA.docs.forEach(function (d) {
    docById[d.id] = d;
    docByPath[d.path] = d;
    docByPath[d.path.replace(/^\.ai\//, '')] = d;
  });

  var taskById = {};
  DATA.tasks.forEach(function (t) { taskById[t.id] = t; });

  var featureByKey = {};
  DATA.features.forEach(function (f) { featureByKey[f.key] = f; });

  // doc -> tasks that name it, and doc -> incoming graph edges
  var tasksByDoc = {};
  DATA.tasks.forEach(function (t) {
    t.rel.relatedDocs.forEach(function (id) {
      (tasksByDoc[id] = tasksByDoc[id] || []).push(t.id);
    });
  });

  var allGraphEdges = [];
  var graphEdgeSeen = {};
  (DATA.graph.edges || []).concat(DATA.graph.bridges || []).forEach(function (edge) {
    if (!edge || !edge.source || !edge.target) return;
    var key = [edge.source, edge.target, edge.type, edge.provenance, edge.bridge ? 'bridge' : 'edge'].join('\u0000');
    if (graphEdgeSeen[key]) return;
    graphEdgeSeen[key] = true;
    allGraphEdges.push(edge);
  });

  var edgesByNode = {};
  allGraphEdges.forEach(function (e) {
    (edgesByNode[e.source] = edgesByNode[e.source] || []).push({ other: e.target, dir: 'out', edge: e });
    (edgesByNode[e.target] = edgesByNode[e.target] || []).push({ other: e.source, dir: 'in', edge: e });
  });

  var DOC_GROUP_ORDER = [
    'Project & Architecture', 'Rules & Governance', 'Roles & Workflows',
    'Decisions & History', 'Knowledge & Memory', 'Skills & Craft', 'Specs & Templates', 'Other'
  ];

  var TYPE_LABEL = {
    'agent': 'Agent', 'workflow': 'Workflow', 'rule': 'Rule', 'project-doc': 'Project doc',
    'decision': 'Decision', 'memory': 'Memory', 'migration': 'Migration', 'skill': 'Skill',
    'spec': 'Spec'
  };

  var LIFE_LABEL = { queued: 'Queued', active: 'Active', done: 'Done', archived: 'Archived', unknown: 'Unfiled' };
  var LIFE_ORDER = ['active', 'queued', 'done', 'archived', 'unknown'];


  var CORPUS_LABEL = { product: 'Product', control: 'Control Plane' };

  function docCorpus(doc) {
    var raw = String((doc && (doc.corpus || doc.corpusKey || doc.audience)) || '').toLowerCase();
    if (raw === 'control' || raw.indexOf('control') !== -1 || raw.indexOf('.ai') !== -1) return 'control';
    if (raw === 'product' || raw.indexOf('user') !== -1) return 'product';
    return doc && /^\.ai\//.test(doc.path || '') ? 'control' : 'product';
  }

  function queryCorpus(value) {
    value = String(value || '').toLowerCase();
    if (value === 'control' || value === 'control-plane') return 'control';
    if (value === 'product' || value === 'product') return 'product';
    return '';
  }

  function corpusUrlValue(value) {
    return value === 'control' ? 'control-plane' : 'product';
  }

  function currentCorpus() {
    var requested = queryCorpus(state.query.corpus);
    if (requested) return requested;
    return DATA.docs.some(function (doc) { return docCorpus(doc) === 'product'; }) ? 'product' : 'control';
  }

  function documentForSelection(selection) {
    var value = String(selection || '');
    if (!value) return null;
    var tail = value.indexOf('::') === -1 ? value : value.split('::').pop();
    if (docById[value]) return docById[value];
    if (docById[tail]) return docById[tail];
    for (var i = 0; i < DATA.docs.length; i++) {
      if (DATA.docs[i].namespaceId === value) return DATA.docs[i];
    }
    return null;
  }

  function defaultDocumentForCorpus(corpus) {
    var preferred = documentForSelection(
      corpus === 'product' ? '000-product-vision' : 'project-decisions'
    );
    if (preferred && docCorpus(preferred) === corpus) return preferred;
    for (var i = 0; i < DATA.docs.length; i++) {
      if (docCorpus(DATA.docs[i]) === corpus) return DATA.docs[i];
    }
    return null;
  }

  function isBridgeEdge(edge) {
    if (edge && (edge.bridge === true || edge.crossCorpus === true || edge.provenance === 'bridge')) return true;
    var source = docById[edge.source];
    var target = docById[edge.target];
    return !!(source && target && docCorpus(source) !== docCorpus(target));
  }

  function docsGraphHash(selection, extra) {
    var query = {};
    Object.keys(state.query || {}).forEach(function (key) { query[key] = state.query[key]; });
    query.view = 'graph';
    query.corpus = corpusUrlValue(queryCorpus(query.corpus) || currentCorpus());
    if (selection !== undefined) query.sel = selection || '';
    Object.keys(extra || {}).forEach(function (key) { query[key] = extra[key]; });
    return buildHash('docs', '', query);
  }

  function docsLibraryHash(selection, extra) {
    var query = {};
    Object.keys(state.query || {}).forEach(function (key) { query[key] = state.query[key]; });
    if (query.view === 'graph') delete query.view;
    query.corpus = corpusUrlValue(queryCorpus(query.corpus) || currentCorpus());
    if (selection !== undefined) query.sel = selection || '';
    Object.keys(extra || {}).forEach(function (key) { query[key] = extra[key]; });
    return buildHash('docs', '', query);
  }

  function documentSelectionStateMarkup(selected) {
    return selected
      ? 'Selection retained: <strong>' + esc(selected.displayTitle) + '</strong> ? ' +
          esc(CORPUS_LABEL[docCorpus(selected)])
      : '';
  }

  function documentSurfaceBar(view, corpus) {
    var selected = documentForSelection(state.query.sel);
    return '<section class="document-surface-bar" aria-label="Document corpus and view">' +
      '<div class="document-surface-copy"><span class="eyebrow">Documents</span>' +
        '<strong>' + esc(CORPUS_LABEL[corpus]) + '</strong>' +
        '<span class="hint">' + (corpus === 'product'
          ? 'Technical, software, and user-facing product knowledge'
          : 'Governance, roles, workflows, decisions, and task law') + '</span></div>' +
      '<div class="document-surface-actions">' +
        '<div class="view-switch corpus-switch" role="group" aria-label="Document corpus">' +
          ['product', 'control'].map(function (key) {
            var count = DATA.docs.filter(function (doc) { return docCorpus(doc) === key; }).length;
            return '<button type="button" data-doc-corpus="' + corpusUrlValue(key) + '" aria-pressed="' + (corpus === key) + '">' +
              esc(CORPUS_LABEL[key]) + '<span class="switch-count">' + count + '</span></button>';
          }).join('') + '</div>' +
        '<div class="view-switch" role="group" aria-label="Document view">' +
          '<button type="button" data-doc-view="library" aria-pressed="' + (view === 'library') + '">Library</button>' +
          '<button type="button" data-doc-view="graph" aria-pressed="' + (view === 'graph') + '">Graph</button>' +
        '</div>' +
        (view === 'graph'
          ? '<label class="bridge-toggle"><input type="checkbox" data-doc-bridges' +
              (state.query.bridges === '1' ? ' checked' : '') + '> Cross-corpus bridges</label>'
          : '') +
      '</div>' +
      '<p class="document-selection-state" data-doc-selection-state' +
        (selected ? '' : ' hidden') + '>' +
        documentSelectionStateMarkup(selected) + '</p>' +
    '</section>';
  }

  function wireDocumentSurface() {
    main.querySelectorAll('[data-doc-corpus]').forEach(function (button) {
      button.addEventListener('click', function () { setQuery({ corpus: button.getAttribute('data-doc-corpus') }); });
    });
    main.querySelectorAll('[data-doc-view]').forEach(function (button) {
      button.addEventListener('click', function () {
        setQuery({ view: button.getAttribute('data-doc-view') === 'graph' ? 'graph' : '' });
      });
    });
    var bridge = main.querySelector('[data-doc-bridges]');
    if (bridge) bridge.addEventListener('change', function () { setQuery({ bridges: bridge.checked ? '1' : '' }); });
  }

  // ------------------------------------------------------------------ utils
  function esc(s) { return MD.escape(s == null ? '' : s); }

  function el(html) {
    var wrap = document.createElement('div');
    wrap.innerHTML = html;
    return wrap.firstElementChild;
  }

  function bytes(n) {
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(n < 10240 ? 1 : 0) + ' KB';
    return (n / 1048576).toFixed(1) + ' MB';
  }

  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }

  function chip(hueKey, label, opts) {
    opts = opts || {};
    return '<span class="chip' + (opts.plain ? ' chip-plain' : '') + '" style="--h: var(--h-' +
      esc(hueKey) + ', 220)"' + (opts.title ? ' title="' + esc(opts.title) + '"' : '') + '>' +
      (opts.dot ? '<span class="chip-dot"></span>' : '') + esc(label) + '</span>';
  }

  function highlight(text, terms) {
    var out = esc(text);
    if (!terms || !terms.length) return out;
    terms.forEach(function (term) {
      if (term.length < 2) return;
      var re = new RegExp('(' + term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig');
      out = out.replace(re, '<mark>$1</mark>');
    });
    return out;
  }

  // ----------------------------------------------------------------- router
  // Open on Overview: it orients a first-time reader across every truth system the plane has,
  // and it is the one screen that is meaningful whether or not the optional Project
  // Intelligence capability is configured.
  function defaultScreen() { return 'home'; }

  var state = { screen: defaultScreen(), param: '', query: {}, anchor: '' };
  var lastQuery = { project: '', docs: '', tasks: '', graph: '' };

  function parseHash() {
    var raw = location.hash.replace(/^#/, '');
    if (!raw) return { screen: defaultScreen(), param: '', query: {}, anchor: '' };
    var anchor = '';
    var hashIndex = raw.indexOf('#');
    if (hashIndex !== -1) { anchor = raw.slice(hashIndex + 1); raw = raw.slice(0, hashIndex); }
    var queryString = '';
    var qIndex = raw.indexOf('?');
    if (qIndex !== -1) { queryString = raw.slice(qIndex + 1); raw = raw.slice(0, qIndex); }
    var parts = raw.replace(/^\//, '').split('/');
    var query = {};
    queryString.split('&').forEach(function (pair) {
      if (!pair) return;
      var kv = pair.split('=');
      query[decodeURIComponent(kv[0])] = decodeURIComponent((kv[1] || '').replace(/\+/g, ' '));
    });
    return {
      screen: parts[0] || defaultScreen(),
      param: parts.slice(1).map(decodeURIComponent).join('/'),
      query: query,
      anchor: anchor
    };
  }

  function buildHash(screen, param, query, anchor) {
    var out = '#/' + screen + (param ? '/' + encodeURIComponent(param) : '');
    var pairs = [];
    Object.keys(query || {}).forEach(function (k) {
      var v = query[k];
      if (v === '' || v == null) return;
      pairs.push(encodeURIComponent(k) + '=' + encodeURIComponent(v));
    });
    if (pairs.length) out += '?' + pairs.join('&');
    if (anchor) out += '#' + anchor;
    return out;
  }

  function go(screen, param, query, opts) {
    var hash = buildHash(screen, param, query);
    if (opts && opts.replace) location.replace(hash);
    else location.hash = hash;
  }

  function setQuery(patch, replace) {
    var next = {};
    Object.keys(state.query).forEach(function (k) { next[k] = state.query[k]; });
    Object.keys(patch).forEach(function (k) {
      if (patch[k] === '' || patch[k] == null) delete next[k];
      else next[k] = patch[k];
    });
    go(state.screen, state.param, next, { replace: replace });
  }

  // ------------------------------------------------------------------ theme
  var THEMES = [
    { key: '', label: '◐ System' },
    { key: 'light', label: '☀ Light' },
    { key: 'dark', label: '☾ Dark' }
  ];
  var themeIndex = 0;
  var themeButton = document.getElementById('theme-toggle');

  // A `theme=` query parameter on any route forces that theme for the session,
  // so a link (or a screenshot command) can pin the appearance it was written
  // for. Ordinary navigation keeps whatever the toggle chose without writing
  // the preference into every URL.
  function adoptThemeFromUrl(query) {
    if (!query || !query.theme) return;
    for (var i = 0; i < THEMES.length; i++) {
      if (THEMES[i].key === query.theme) {
        if (i !== themeIndex) { themeIndex = i; applyTheme(); }
        return;
      }
    }
  }

  function applyTheme() {
    var theme = THEMES[themeIndex];
    if (theme.key) document.documentElement.setAttribute('data-theme', theme.key);
    else document.documentElement.removeAttribute('data-theme');
    themeButton.textContent = theme.label;
    themeButton.setAttribute('aria-label', 'Colour theme: ' + theme.label.slice(2).trim() +
      '. Activate to change.');
    if (window.CPGraph) window.CPGraph.recolour();
    if (window.CPProjectUI) window.CPProjectUI.recolour();
  }
  themeButton.addEventListener('click', function () {
    themeIndex = (themeIndex + 1) % THEMES.length;
    applyTheme();
  });
  applyTheme();

  // ----------------------------------------------------------------- search
  function tokens(q) {
    return (q || '').toLowerCase().split(/\s+/).filter(function (t) { return t.length > 1; });
  }

  function scoreDoc(doc, terms) {
    var title = doc.title.toLowerCase();
    var id = doc.id.toLowerCase();
    var summary = doc.summary.toLowerCase();
    var body = null;
    var score = 0;
    for (var i = 0; i < terms.length; i++) {
      var t = terms[i];
      var hit = 0;
      if (title.indexOf(t) !== -1) hit += 12;
      if (id.indexOf(t) !== -1) hit += 8;
      if (doc.type.indexOf(t) !== -1) hit += 4;
      if (summary.indexOf(t) !== -1) hit += 4;
      if (!hit) {
        if (body === null) body = doc.body.toLowerCase();
        if (body.indexOf(t) !== -1) hit += 1;
      }
      if (!hit) return 0;
      score += hit;
    }
    return score;
  }

  function taskHaystack(task) {
    if (task._hay) return task._hay;
    var p = taskPresentation(task);
    var footprint = p.technicalFootprint || {};
    var rel = task.rel || {};
    var resolved = rel.resolved || {};
    var relationshipIds = [];
    ['dependsOn', 'blockedBy', 'decomposedInto', 'parallelWith', 'informedBy']
      .forEach(function (key) {
        (Array.isArray(resolved[key]) ? resolved[key] : []).forEach(function (value) {
          relationshipIds.push(typeof value === 'string' ? value : (value.id || value.ref || ''));
        });
      });
    ['blocks', 'slices', 'relatedDocs'].forEach(function (key) {
      (Array.isArray(rel[key]) ? rel[key] : []).forEach(function (value) {
        relationshipIds.push(typeof value === 'string' ? value : (value.id || value.ref || ''));
      });
    });
    if (rel.parent) relationshipIds.push(rel.parent);
    task._hay = [
      task.id, taskTitle(task), taskFeatureLabel(task), task.lifecycle, taskRisk(task), taskTool(task),
      p.purpose, p.outcome, arrayText(p.scope), arrayText(p.outOfScope), arrayText(p.acceptance),
      areaText(footprint.touched), areaText(footprint.offLimits), relationshipIds.join(' ')
    ].join(' \n ').toLowerCase();
    return task._hay;
  }

  function scoreTask(task, terms) {
    var title = taskTitle(task).toLowerCase();
    var id = task.id.toLowerCase();
    var score = 0;
    for (var i = 0; i < terms.length; i++) {
      var t = terms[i];
      var hit = 0;
      if (id.indexOf(t) !== -1) hit += 12;
      if (title.indexOf(t) !== -1) hit += 10;
      if (taskFeatureLabel(task).toLowerCase().indexOf(t) !== -1) hit += 5;
      if (!hit && taskHaystack(task).indexOf(t) !== -1) hit += 1;
      if (!hit) return 0;
      score += hit;
    }
    return score;
  }

  // ------------------------------------------------------- markdown linking
  function makeLinkResolver(fromPath) {
    return function (href) {
      if (!href) return null;
      if (/^(https?:|mailto:)/i.test(href)) return href;
      if (href.charAt(0) === '#') return href;
      var clean = href.split('#')[0].replace(/^\.\//, '');
      var frag = href.indexOf('#') !== -1 ? href.slice(href.indexOf('#') + 1) : '';
      var base = (fromPath || '').replace(/\/[^/]*$/, '');
      var candidates = [clean, base + '/' + clean, '.ai/' + clean];
      // resolve ../
      var normalised = [];
      candidates.forEach(function (c) {
        var parts = [];
        c.split('/').forEach(function (seg) {
          if (seg === '..') parts.pop();
          else if (seg && seg !== '.') parts.push(seg);
        });
        normalised.push(parts.join('/'));
      });
      for (var i = 0; i < normalised.length; i++) {
        var hit = docByPath[normalised[i]];
        if (hit) return buildHash('doc', hit.id, {}, frag);
      }
      var taskMatch = /task_[0-9a-z_]+/i.exec(clean);
      if (taskMatch && taskById[taskMatch[0]]) return buildHash('task', taskMatch[0], {});
      return null;
    };
  }

  function renderMarkdown(source, fromPath, liftTitle) {
    return MD.render(source, {
      linkResolver: makeLinkResolver(fromPath),
      liftTitle: !!liftTitle
    });
  }

  // ------------------------------------------------------------- components
  function docRow(doc, terms, compact, options) {
    options = options || {};
    var relCount = (edgesByNode[doc.id] || []).length;
    var taskCount = (tasksByDoc[doc.id] || []).length;
    var meta = compact
      ? chip(doc.type, TYPE_LABEL[doc.type] || doc.type, { dot: true })
      : chip(doc.type, TYPE_LABEL[doc.type] || doc.type, { dot: true }) +
        (doc.status !== 'active' ? chip('archived', doc.status, { plain: true }) : '') +
        '<span class="micro">' + bytes(doc.bytes) + '</span>' +
        (relCount ? '<span class="micro" title="linked documents">↔ ' + relCount + '</span>' : '') +
        (taskCount ? '<span class="micro" title="task contracts naming this document">⊞ ' + taskCount + '</span>' : '');
    var selected = options.selected === doc.id;
    var href = options.inline ? docsLibraryHash(doc.id) : buildHash('doc', doc.id, {});
    return '<a class="row' + (compact ? ' row-compact' : '') + (selected ? ' is-selected' : '') +
      '" style="--h: var(--h-' + esc(doc.type) + ', 220)" href="' + href + '"' +
      (selected ? ' aria-current="true"' : '') + '>' +
      '<span class="title">' + highlight(doc.displayTitle, terms) + '</span>' +
      '<span class="meta">' + meta + '</span>' +
      (doc.summary ? '<span class="summary">' + highlight(doc.summary, terms) + '</span>' : '') +
      '</a>';
  }

  function clip(text, max) {
    if (!text || text.length <= max) return text || '';
    var cut = text.slice(0, max);
    var space = cut.lastIndexOf(' ');
    return (space > max * 0.6 ? cut.slice(0, space) : cut) + '…';
  }

  function taskPresentation(task) {
    return window.CPTaskRich.presentation(task);
  }

  function taskTitle(task) {
    var p = taskPresentation(task);
    return typeof p.title === 'string' && p.title ? p.title : task.id;
  }

  function taskFeatureLabel(task) {
    var p = taskPresentation(task);
    return typeof p.featureLabel === 'string' ? p.featureLabel : '';
  }

  function taskFeatureKey(task) {
    var p = taskPresentation(task);
    return typeof p.featureKey === 'string' ? p.featureKey : '';
  }

  function arrayText(value) {
    return Array.isArray(value) ? value.join(' ') : '';
  }

  function taskRisk(task) {
    return task && task.context && typeof task.context.risk === 'string' ? task.context.risk : '';
  }

  function taskTool(task) {
    return task && task.context && typeof task.context.preferred_tool === 'string'
      ? task.context.preferred_tool : '';
  }

  function taskReviewTool(task) {
    return task && task.context && typeof task.context.review_tool === 'string'
      ? task.context.review_tool : '';
  }

  function presentationAreas(task, key) {
    var footprint = taskPresentation(task).technicalFootprint || {};
    return Array.isArray(footprint[key]) ? footprint[key].filter(function (area) {
      return area && area.key !== 'own-task-folder' && area.label;
    }) : [];
  }

  function areaText(areas) {
    return (Array.isArray(areas) ? areas : []).map(function (area) {
      return area && area.label ? area.label : '';
    }).join(' ');
  }

  function taskPortfolioAreas() {
    var byKey = {};
    DATA.tasks.forEach(function (task) {
      presentationAreas(task, 'touched').forEach(function (area) {
        var bucket = byKey[area.key] || { key: area.key, label: area.label, taskCount: 0 };
        bucket.taskCount += 1;
        byKey[area.key] = bucket;
      });
    });
    return Object.keys(byKey).map(function (key) { return byKey[key]; })
      .sort(function (a, b) { return b.taskCount - a.taskCount || a.label.localeCompare(b.label); });
  }

  function presentationSummary(task) {
    var p = taskPresentation(task);
    if (p.state === 'authored') return p.purpose || p.outcome || '';
    var unavailable = p.unavailable || {};
    return unavailable.label || 'Human presentation unavailable';
  }

  function taskSourceHash(task) {
    return buildHash('task', task.id, { view: 'source' });
  }

  function taskRow(task, terms) {
    var p = taskPresentation(task);
    var authored = p.state === 'authored';
    var feature = taskFeatureLabel(task) || '(no feature recorded)';
    var areas = presentationAreas(task, 'touched');
    var summary = presentationSummary(task);
    return '<article class="row" data-state="' + esc(p.state) + '" data-title-state="' +
      esc(p.titleState || 'derived') + '" data-feature-state="' + esc(p.featureState || 'unavailable') +
      '" style="--h: var(--h-' + esc(task.lifecycle) + ', 220)">' +
      '<a class="title" style="color:inherit;text-decoration:none" href="' +
        buildHash('task', task.id, {}) + '">' + highlight(taskTitle(task), terms) + '</a>' +
      '<span class="meta">' +
        (areas.length
          ? '<span class="micro" title="Derived technical footprint">' +
            esc(clip(areas[0].label, 26)) +
            (areas.length > 1 ? ' +' + (areas.length - 1) : '') + '</span>'
          : '') +
        chip(task.lifecycle, LIFE_LABEL[task.lifecycle],
          { dot: true, title: 'Lifecycle derived from governed folder state' }) +
        chip(taskRisk(task) || 'archived', taskRisk(task) || 'risk n/r',
          { plain: !taskRisk(task), title: 'risk' }) +
        '<span class="micro">' + esc(taskTool(task) || '—') + '</span>' +
      '</span>' +
      '<span class="summary"><code style="font-size:11px">' + esc(task.id) + '</code> · ' +
        highlight(feature, terms) +
        (summary ? ' — ' + highlight(clip(summary, 150), terms) : '') +
        (!authored ? ' · <a class="btn" href="' + taskSourceHash(task) + '">' +
          esc((p.unavailable && p.unavailable.sourceActionLabel) || 'Open Source') + '</a>' : '') +
      '</span>' +
      '</article>';
  }

  function emptyState(what, onReset) {
    return '<div class="card empty"><strong>No ' + esc(what) + ' match these filters.</strong>' +
      '<p>Try a shorter search, or clear the active filters.</p>' +
      '<button class="btn" type="button" data-action="' + esc(onReset) + '">Clear all filters</button></div>';
  }

  function activeFilterBar(filters) {
    var live = filters.filter(function (f) { return f.value; });
    if (!live.length) return '';
    return '<div class="active-filters">' + live.map(function (f) {
      return '<button class="filter-pill" type="button" data-clear="' + esc(f.key) + '">' +
        esc(f.label) + ': ' + esc(f.display || f.value) + ' <span class="x" aria-hidden="true">×</span>' +
        '<span class="sr-only">Remove filter</span></button>';
    }).join('') + '</div>';
  }

  // ------------------------------------------------------------------- HOME
  function screenHome() {
    var c = DATA.counts;
    var p = PROJECT;
    var byGroup = {};
    var docBytes = 0;
    DATA.docs.forEach(function (d) {
      (byGroup[d.group] = byGroup[d.group] || []).push(d);
      docBytes += d.bytes;
    });

    var paths = [
      { h: 'project-doc', q: 'Understand the repository', t: 'See what the agent actually knows',
        d: 'Expand the real indexed workspace from crate to module, file and semantic-owner symbol.',
        href: buildHash('project', '', {}) },
      { h: 'rule', q: 'About to change code', t: 'The rules that will gate you',
        d: 'Twelve binding rules cover task contracts, isolation, verification, QA evidence and visual proof.',
        href: buildHash('docs', '', {
          corpus: 'control-plane',
          group: 'Rules & Governance',
          sel: 'rule-task-contracts'
        }) },
      { h: 'workflow', q: 'Running a phase', t: 'Plan, dispatch, execute, review',
        d: 'Eleven workflows and four agent roles describe who does what, in which order, with which evidence.',
        href: buildHash('docs', '', {
          corpus: 'control-plane',
          group: 'Roles & Workflows',
          sel: 'workflow-planning'
        }) },
      { h: 'decision', q: 'Asking “why is it like this?”', t: 'The decision trail',
        d: 'Every architectural turn is logged with its date and the reasoning that produced it.',
        href: buildHash('doc', 'project-decisions', {}) }
    ];

    var lifecycleBar = LIFE_ORDER.filter(function (k) { return c.lifecycle[k]; }).map(function (k) {
      var n = c.lifecycle[k];
      return '<a class="facet-btn" style="--h: var(--h-' + k + ', 220)" href="' +
        buildHash('tasks', '', { life: k }) + '">' +
        '<span class="swatch"></span>' + LIFE_LABEL[k] + '<span class="n">' + n + '</span></a>';
    }).join('');

    var topFeatures = DATA.features.filter(function (f) { return f.count > 1; }).slice(0, 12);
    var queuedTasks = DATA.tasks.filter(function (t) { return t.lifecycle === 'queued'; })
      .slice().sort(function (a, b) { return numberOf(b) - numberOf(a); });
    var portfolioAreas = taskPortfolioAreas();

    var html =
      '<div class="page">' +
      '<div class="hero">' +
        '<div>' +
          '<p class="eyebrow">Agent control plane · ' + esc(DATA.meta.sourceCommit) + '</p>' +
          '<h1>See the project model before you trust the plan.</h1>' +
          '<p>Three peer truth systems stay distinct and cross-linked. <strong>Project Intelligence</strong> ' +
          'shows indexed code and honest resolution limits. <strong>Control-plane documents</strong> hold standing ' +
          'law and knowledge. <strong>Task contracts</strong> describe bounded feature work and scope.</p>' +
        '</div>' +
        '<div class="stat-grid">' +
          '<a class="card stat" href="' + buildHash('project', '', {}) + '" style="text-decoration:none;color:inherit">' +
            '<span class="n">' + p.counts.nodes.toLocaleString() + '</span><span class="l">Semantic symbols</span>' +
            '<span class="sub">' + p.counts.files + ' files · ' + p.counts.edges.toLocaleString() + ' resolved edges</span></a>' +
          '<a class="card stat" href="' + buildHash('tasks', '', {}) + '" style="text-decoration:none;color:inherit">' +
            '<span class="n">' + c.tasks + '</span><span class="l">Task contracts</span>' +
            '<span class="sub">' + c.features + ' features · 4 lifecycle states</span></a>' +
          '<a class="card stat" href="' + buildHash('docs', '', { view: 'graph', corpus: corpusUrlValue(currentCorpus()) }) + '" style="text-decoration:none;color:inherit">' +
            '<span class="n">' + c.graphEdges + '</span><span class="l">Document links</span>' +
            '<span class="sub">authored, structural and referenced</span></a>' +
          '<div class="card stat">' +
            '<span class="n">' + c.lifecycle.queued + '</span><span class="l">Queued now</span>' +
            '<span class="sub">' + c.lifecycle.done + ' done · ' + c.lifecycle.archived + ' archived</span></div>' +
        '</div>' +
      '</div>' +

      '<section class="section project-home">' +
        '<div class="section-head"><h2>Project Intelligence</h2>' +
        '<span class="hint">' + p.status.label + ' · schema v' + p.meta.schemaVersion + ' · ' + p.counts.pending.toLocaleString() + ' pending kept explicit</span>' +
        '<span class="grow"></span><a href="' + buildHash('project', '', {}) + '">Open the repository graph →</a></div>' +
        '<div class="group-grid project-cluster-cards">' + p.clusters.slice(0, 8).map(function (cluster) {
          var purpose = cluster.purpose;
          var purposeText = typeof purpose === 'string' ? purpose : purpose && purpose.value;
          return '<a class="card group cluster-card" href="' + buildHash('project', '', { scope: 'crate', crate: cluster.id }) + '">' +
            '<header><h3>' + esc(cluster.label) + '</h3><span class="count">' + cluster.nodes.toLocaleString() + '</span></header>' +
            '<p class="cluster-purpose' + (purposeText ? '' : ' is-missing') + '">' +
              esc(purposeText || 'No authored crate purpose is available.') + '</p>' +
            '<p class="ghint">' + cluster.files + ' files · ' + cluster.resolvedEdges.toLocaleString() + ' resolved edge touches · ' + cluster.pending.toLocaleString() + ' pending</p></a>';
        }).join('') + '</div>' +
      '</section>' +      '<section class="section">' +
        '<div class="section-head"><h2>Where do you want to start?</h2>' +
        '<span class="hint">Four common reasons to open this library</span></div>' +
        '<div class="paths">' + paths.map(function (p) {
          return '<a class="path" style="--h: var(--h-' + p.h + ')" href="' + p.href + '">' +
            '<span class="q">' + esc(p.q) + '</span>' +
            '<span class="t">' + esc(p.t) + '</span>' +
            '<span class="d">' + esc(p.d) + '</span></a>';
        }).join('') + '</div>' +
      '</section>' +

      '<section class="section">' +
        '<div class="section-head"><h2>Control-plane documents</h2>' +
        '<span class="hint">Standing law, knowledge and architecture</span>' +
        '<span class="grow"></span><a href="' + buildHash('docs', '', {}) + '">Open the catalog →</a></div>' +
        '<div class="group-grid">' +
        DOC_GROUP_ORDER.filter(function (g) { return byGroup[g]; }).map(function (group) {
          var docs = byGroup[group].slice().sort(function (a, b) { return b.bytes - a.bytes; });
          var shown = docs.slice(0, 5);
          return '<div class="card group">' +
            '<header><h3>' + esc(group) + '</h3><span class="count">' + docs.length + '</span></header>' +
            '<p class="ghint">' + esc(docs[0].groupHint) + '</p>' +
            '<div class="rows">' + shown.map(function (d) { return docRow(d, [], true); }).join('') + '</div>' +
            (docs.length > shown.length ?
              '<p class="ghint" style="padding:8px 4px 4px"><a href="' +
              buildHash('docs', '', { group: group }) + '">' +
              (docs.length - shown.length) + ' more in this section →</a></p>' : '<p class="ghint"></p>') +
            '</div>';
        }).join('') +
        '</div>' +
      '</section>' +

      '<section class="section">' +
        '<div class="section-head"><h2>Tasks &amp; features</h2>' +
        '<span class="hint">Queued contracts are the active attention surface</span>' +
        '<span class="grow"></span><a href="' + buildHash('tasks', '', {}) + '">Open the task catalog →</a></div>' +
        '<div class="task-focus-layout">' +
          '<article class="card queue-focus">' +
            '<header class="queue-focus-head">' +
              '<div><p class="eyebrow">Attention queue</p><h3>Queued work</h3>' +
              '<p class="ghint">Contracts waiting for execution, ordered by newest task id. ' +
              'Folder state determines lifecycle; ordering does not imply product priority.</p></div>' +
              '<a class="queue-count" href="' + buildHash('tasks', '', { life: 'queued' }) + '">' +
                '<strong>' + c.lifecycle.queued + '</strong><span>queued</span></a>' +
            '</header>' +
            '<div class="queue-focus-grid">' + queuedTasks.slice(0, 6).map(function (t, index) {
              var p = taskPresentation(t);
              var authored = p.state === 'authored';
              var feature = taskFeatureLabel(t) || '(feature not recorded)';
              var areas = presentationAreas(t, 'touched');
              var summary = presentationSummary(t);
              return '<a class="queue-focus-item' + (index === 0 ? ' lead' : '') +
                '" data-state="' + esc(p.state) + '" data-title-state="' +
                esc(p.titleState || 'derived') + '" data-feature-state="' +
                esc(p.featureState || 'unavailable') + '" href="' +
                (authored ? buildHash('task', t.id, {}) : taskSourceHash(t)) + '">' +
                '<div class="queue-item-top"><span class="queue-id">' + esc(t.id) + '</span>' +
                  (index === 0 ? '<span class="queue-newest">Newest contract</span>' : '') + '</div>' +
                '<h4>' + esc(taskTitle(t)) + '</h4>' +
                '<p>' + esc(clip(summary, index === 0 ? 230 : 150)) + '</p>' +
                '<div class="queue-meta">' +
                  chip(t.lifecycle, LIFE_LABEL[t.lifecycle], { dot: true }) +
                  chip(taskRisk(t) || 'archived', taskRisk(t) || 'risk not recorded',
                    { plain: !taskRisk(t), title: 'risk' }) +
                  '<span class="queue-feature">' + esc(feature) + '</span>' +
                  (areas.length ? '<span class="queue-area">' + esc(areas[0].label) +
                    (areas.length > 1 ? ' +' + (areas.length - 1) : '') + '</span>' : '') +
                  (!authored ? '<span class="btn">' +
                    esc((p.unavailable && p.unavailable.sourceActionLabel) || 'Open Source') + '</span>' : '') +
                '</div>' +
              '</a>';
            }).join('') + '</div>' +
            '<a class="queue-more" href="' + buildHash('tasks', '', { life: 'queued' }) + '">' +
              'Review all ' + c.lifecycle.queued + ' queued contracts →</a>' +
          '</article>' +
          '<aside class="task-focus-rail" aria-label="Task portfolio summaries">' +
            '<div class="card group task-summary-card">' +
              '<header><h3>Lifecycle</h3><span class="count">' + c.tasks + '</span></header>' +
              '<p class="ghint">State is derived from each task folder, never its free-form status.</p>' +
              '<div class="facet-list">' + lifecycleBar + '</div>' +
            '</div>' +
            '<div class="card group task-summary-card">' +
              '<header><h3>Feature signals</h3><span class="count">' + c.features + '</span></header>' +
              '<p class="ghint">Current feature values are display labels; stable feature identity is still needed.</p>' +
              '<div class="facet-list">' + topFeatures.slice(0, 6).map(function (f) {
                return '<a class="facet-btn" href="' + buildHash('tasks', '', { feature: f.key }) + '">' +
                  esc(f.label) + '<span class="n">' + f.count + '</span></a>';
              }).join('') + '</div>' +
            '</div>' +
            '<div class="card group task-summary-card">' +
              '<header><h3>Technical footprint</h3><span class="count">' + portfolioAreas.length + '</span></header>' +
              '<p class="ghint">Derived technical area labels, separate from authored feature scope.</p>' +
              '<div class="facet-list">' +
                portfolioAreas
                  .slice(0, 6).map(function (a) {
                    return '<a class="facet-btn" href="' + buildHash('tasks', '', { area: a.key }) + '">' +
                      esc(a.label) + '<span class="n">' + a.taskCount + '</span></a>';
                  }).join('') +
              '</div>' +
            '</div>' +
          '</aside>' +
        '</div>' +
      '</section>' +
      '<p class="result-count">Snapshot built from the working tree at <code>' +
      esc(DATA.meta.sourceCommit) + '</code>. ' + esc(DATA.meta.note) + '</p>' +
      '</div>';

    main.innerHTML = html;
  }

  function numberOf(task) {
    var m = /^task_(\d+)/.exec(task.id);
    return m ? parseInt(m[1], 10) : 0;
  }

  function libraryDocumentReader(doc, corpus) {
    if (!doc) {
      return '<aside class="document-inline-reader" data-inline-document aria-label="Selected document">' +
        '<div class="placeholder"><p><strong>No document is available in this corpus.</strong></p></div></aside>';
    }

    var rendered = renderMarkdown(doc.body, doc.path, true);
    var links = (edgesByNode[doc.id] || []).filter(function (link) {
      return state.query.bridges === '1' || !isBridgeEdge(link.edge);
    });
    var relatedTasks = tasksByDoc[doc.id] || [];

    function relationRows(items) {
      if (!items.length) return '<p class="rel-kind">No linked documents in this view.</p>';
      return '<ul class="inline-document-relations">' + items.map(function (link) {
        var other = documentForSelection(link.other);
        var targetCorpus = other ? docCorpus(other) : corpus;
        return '<li><a href="' + docsLibraryHash(link.other, {
          corpus: corpusUrlValue(targetCorpus)
        }) + '">' + esc(other ? other.displayTitle : link.other) + '</a>' +
          '<span>' + esc(relationLabel(link.edge.type, link.dir)) + ' &middot; ' +
            esc(link.edge.provenance) + (isBridgeEdge(link.edge) ? ' &middot; bridge' : '') + '</span></li>';
      }).join('') + '</ul>';
    }

    return '<article class="document-inline-reader" data-inline-document="' + esc(doc.id) +
      '" aria-labelledby="inline-document-title">' +
      '<header class="graph-reader-head document-inline-head">' +
        '<div class="task-badges">' +
          chip(doc.type, TYPE_LABEL[doc.type] || doc.type, { dot: true }) +
          '<span class="micro rel-kind">' + bytes(doc.bytes) + ' &middot; ' +
            plural(rendered.headings.length, 'heading') + '</span>' +
          '<span class="grow"></span>' +
          '<a class="btn" href="' + buildHash('doc', doc.id, {}) + '">Full-page details</a>' +
        '</div>' +
        '<p class="eyebrow">Selected ' + esc(CORPUS_LABEL[docCorpus(doc)]) + ' document</p>' +
        '<h2 id="inline-document-title">' + esc(doc.displayTitle) + '</h2>' +
        (doc.readerGoal ? '<p class="doc-goal">' + esc(doc.readerGoal) + '</p>' : '') +
        '<p class="inline-document-path"><code>' + esc(doc.path) + '</code></p>' +
        (docCorpus(doc) !== corpus
          ? '<p class="inline-document-context">Selection retained while browsing ' +
              esc(CORPUS_LABEL[corpus]) + '.</p>'
          : '') +
      '</header>' +
      '<div class="graph-reader-body document-inline-body">' +
        ((doc.sourceIssues || []).length
          ? '<p class="source-note"><strong>Source file defect.</strong> ' +
              doc.sourceIssues.map(esc).join(' ') + '</p>'
          : '') +
        '<div class="prose">' + rendered.html + '</div>' +
        '<section class="inline-document-followups" aria-label="Document relationships">' +
          '<h3>Relationships</h3>' + relationRows(links) +
          (relatedTasks.length
            ? '<p class="rel-kind"><a href="' + buildHash('tasks', '', { q: doc.path }) + '">' +
                plural(relatedTasks.length, 'task contract') + ' name this file</a></p>'
            : '<p class="rel-kind">No task contract names this file.</p>') +
        '</section>' +
      '</div>' +
    '</article>';
  }

  // --------------------------------------------------------- DOCUMENT LIST
  function screenDocs() {
    var corpus = currentCorpus();
    var selected = documentForSelection(state.query.sel);
    if (!selected) {
      selected = defaultDocumentForCorpus(corpus);
      if (selected) {
        var selectedQuery = {};
        Object.keys(state.query || {}).forEach(function (key) { selectedQuery[key] = state.query[key]; });
        selectedQuery.sel = selected.id;
        state.query = selectedQuery;
        go('docs', '', selectedQuery, { replace: true });
      }
    }

    var view = state.query.view === 'graph' ? 'graph' : 'library';
    if (view === 'graph') { screenGraph(); return; }

    var corpusDocs = DATA.docs.filter(function (doc) { return docCorpus(doc) === corpus; });
    var q = state.query.q || '';
    var terms = tokens(q);
    var group = state.query.group || '';
    var type = state.query.type || '';
    var status = state.query.status || '';
    var sort = state.query.sort || 'section';

    var pool = corpusDocs.filter(function (d) {
      if (group && d.group !== group) return false;
      if (type && d.type !== type) return false;
      if (status && d.status !== status) return false;
      return true;
    });
    var results = pool;
    if (terms.length) {
      results = pool.map(function (d) { return { d: d, s: scoreDoc(d, terms) }; })
        .filter(function (r) { return r.s > 0; })
        .sort(function (a, b) { return b.s - a.s; })
        .map(function (r) { return r.d; });
    } else if (sort === 'size') {
      results = pool.slice().sort(function (a, b) { return b.bytes - a.bytes; });
    } else if (sort === 'links') {
      results = pool.slice().sort(function (a, b) {
        return (edgesByNode[b.id] || []).length - (edgesByNode[a.id] || []).length;
      });
    } else if (sort === 'title') {
      results = pool.slice().sort(function (a, b) { return a.title.localeCompare(b.title); });
    }

    function facetCount(field, value) {
      return corpusDocs.filter(function (d) {
        if (group && field !== 'group' && d.group !== group) return false;
        if (type && field !== 'type' && d.type !== type) return false;
        if (status && field !== 'status' && d.status !== status) return false;
        return d[field] === value;
      }).length;
    }

    var groups = DOC_GROUP_ORDER.filter(function (g) {
      return corpusDocs.some(function (d) { return d.group === g; });
    });
    corpusDocs.forEach(function (doc) {
      if (doc.group && groups.indexOf(doc.group) === -1) groups.push(doc.group);
    });
    var typeCount = {};
    corpusDocs.forEach(function (doc) { typeCount[doc.type] = (typeCount[doc.type] || 0) + 1; });
    var types = Object.keys(typeCount).sort();

    var facets =
      '<div class="facets">' +
        '<div class="facet"><h3>Section</h3><div class="facet-list">' +
          groups.map(function (g) {
            return '<button class="facet-btn" type="button" data-facet="group" data-value="' + esc(g) + '"' +
              ' aria-pressed="' + (group === g) + '">' + esc(g) +
              '<span class="n">' + facetCount('group', g) + '</span></button>';
          }).join('') +
        '</div></div>' +
        '<div class="facet"><h3>Type</h3><div class="facet-list">' +
          types.map(function (t) {
            return '<button class="facet-btn" type="button" style="--h: var(--h-' + esc(t) + ', 220)"' +
              ' data-facet="type" data-value="' + esc(t) + '" aria-pressed="' + (type === t) + '">' +
              '<span class="swatch"></span>' + esc(TYPE_LABEL[t] || t) +
              '<span class="n">' + facetCount('type', t) + '</span></button>';
          }).join('') +
        '</div></div>' +
        '<div class="facet"><h3>Status</h3><div class="facet-list">' +
          Array.from(new Set(corpusDocs.map(function (doc) { return doc.status; }).filter(Boolean))).sort().map(function (value) {
            return '<button class="facet-btn" type="button" data-facet="status" data-value="' + esc(value) + '"' +
              ' aria-pressed="' + (status === value) + '">' + esc(value) +
              '<span class="n">' + facetCount('status', value) + '</span></button>';
          }).join('') +
        '</div></div>' +
      '</div>';

    var body;
    if (!results.length) {
      body = emptyState('documents', 'reset-docs');
    } else if (!terms.length && sort === 'section') {
      var byGroup = {};
      results.forEach(function (d) { (byGroup[d.group || 'Other'] = byGroup[d.group || 'Other'] || []).push(d); });
      body = groups.filter(function (g) { return byGroup[g]; }).map(function (g) {
        return '<section class="section" style="margin-bottom:24px">' +
          '<div class="section-head"><h2 style="font-size:var(--step-1)">' + esc(g) + '</h2>' +
          '<span class="hint">' + esc(byGroup[g][0].groupHint || '') + '</span>' +
          '<span class="grow"></span><span class="result-count">' + byGroup[g].length + '</span></div>' +
          '<div class="card list-card"><div class="rows">' +
          byGroup[g].map(function (d) {
            return docRow(d, terms, false, { inline: true, selected: selected && selected.id });
          }).join('') +
          '</div></div></section>';
      }).join('');
    } else {
      body = '<div class="card list-card"><div class="rows">' +
        results.map(function (d) {
          return docRow(d, terms, false, { inline: true, selected: selected && selected.id });
        }).join('') + '</div></div>';
    }

    main.innerHTML =
      '<div class="page documents-page">' + documentSurfaceBar('library', corpus) +
        '<div class="documents-library-shell">' +
        '<section class="documents-library-pane" aria-label="Document library">' +
        '<div class="catalog">' + facets +
        '<div>' +
          '<div class="toolbar">' +
            '<div class="grow"><label class="sr-only" for="docq">Search documents</label>' +
            '<input id="docq" type="search" placeholder="Search titles, ids and full text..." value="' +
            esc(q) + '" style="width:100%"></div>' +
            '<label class="sr-only" for="docsort">Sort</label>' +
            '<select id="docsort"' + (terms.length ? ' disabled title="Sorted by relevance while searching"' : '') + '>' +
              ['section:Grouped by section', 'title:Title A-Z', 'size:Longest first', 'links:Most linked']
                .map(function (o) {
                  var kv = o.split(':');
                  return '<option value="' + kv[0] + '"' + (sort === kv[0] ? ' selected' : '') + '>' + kv[1] + '</option>';
                }).join('') +
            '</select>' +
            '<span class="result-count" role="status">' + results.length + ' of ' + corpusDocs.length + '</span>' +
          '</div>' +
          activeFilterBar([
            { key: 'q', label: 'Search', value: q },
            { key: 'group', label: 'Section', value: group },
            { key: 'type', label: 'Type', value: type, display: TYPE_LABEL[type] || type },
            { key: 'status', label: 'Status', value: status }
          ]) +
          body +
        '</div></div></section>' +
        libraryDocumentReader(selected, corpus) +
        '</div></div>';

    wireCatalog('docq', 'docsort');
    wireDocumentSurface();
  }

  function wireCatalog(searchId, sortId) {
    var search = document.getElementById(searchId);
    if (search) {
      var timer = null;
      search.addEventListener('input', function () {
        clearTimeout(timer);
        var value = search.value;
        timer = setTimeout(function () {
          var pos = search.selectionStart;
          setQuery({ q: value }, true);
          var again = document.getElementById(searchId);
          if (again) { again.focus(); try { again.setSelectionRange(pos, pos); } catch (e) {} }
        }, 160);
      });
    }
    var sort = document.getElementById(sortId);
    if (sort) sort.addEventListener('change', function () { setQuery({ sort: sort.value }); });

    main.querySelectorAll('[data-facet]').forEach(function (button) {
      button.addEventListener('click', function () {
        var key = button.getAttribute('data-facet');
        var value = button.getAttribute('data-value');
        var patch = {};
        patch[key] = state.query[key] === value ? '' : value;
        setQuery(patch);
      });
    });
    main.querySelectorAll('[data-clear]').forEach(function (button) {
      button.addEventListener('click', function () {
        var patch = {};
        patch[button.getAttribute('data-clear')] = '';
        setQuery(patch);
      });
    });
    main.querySelectorAll('[data-action^="reset-"]').forEach(function (button) {
      button.addEventListener('click', function () { go(state.screen, state.param, {}); });
    });
  }

  // ------------------------------------------------------- DOCUMENT READER
  function relationLabel(kind, dir) {
    var map = {
      depends_on: dir === 'out' ? 'depends on' : 'is depended on by',
      enforced_by: dir === 'out' ? 'is enforced by' : 'enforces',
      references: dir === 'out' ? 'references' : 'is referenced by',
      contains: dir === 'out' ? 'contains' : 'is part of',
      relates_to: 'relates to'
    };
    return map[kind] || kind.replace(/_/g, ' ');
  }

  function screenDoc() {
    var doc = docById[state.param];
    if (!doc) return notFound('document', state.param, buildHash('docs', '', {}));
    var view = state.query.view === 'source' ? 'source' : 'read';

    var rendered = renderMarkdown(doc.body, doc.path, true);
    var links = edgesByNode[doc.id] || [];
    var outgoing = links.filter(function (l) { return l.dir === 'out'; });
    var incoming = links.filter(function (l) { return l.dir === 'in'; });
    var relatedTasks = tasksByDoc[doc.id] || [];
    var frontmatter = MD.splitFrontmatter(doc.body).front;

    function linkList(items) {
      if (!items.length) return '<p class="rel-kind">None recorded.</p>';
      return '<ul>' + items.map(function (l) {
        var other = docById[l.other];
        return '<li><a href="' + buildHash('doc', l.other, {}) + '">' +
          esc(other ? other.displayTitle : l.other) + '</a> ' +
          '<span class="rel-kind">— ' + esc(relationLabel(l.edge.type, l.dir)) +
          ' · ' + esc(l.edge.provenance) + '</span></li>';
      }).join('') + '</ul>';
    }

    var toc = rendered.headings.filter(function (h) { return h.level >= 2 && h.level <= 4; });

    main.innerHTML =
      '<div class="page"><div class="reader">' +
      '<div class="reader-main">' +
        '<nav class="breadcrumb" aria-label="Breadcrumb">' +
          '<a href="' + buildHash('home', '', {}) + '">Control plane</a><span class="sep">/</span>' +
          '<a href="' + buildHash('docs', '', {}) + '">Documents</a><span class="sep">/</span>' +
          '<a href="' + buildHash('docs', '', { group: doc.group }) + '">' + esc(doc.group) + '</a>' +
          '<span class="sep">/</span><span>' + esc(TYPE_LABEL[doc.type] || doc.type) + '</span>' +
        '</nav>' +
        '<header class="doc-head">' +
          '<div class="task-badges" style="margin-bottom:10px">' +
            chip(doc.type, TYPE_LABEL[doc.type] || doc.type, { dot: true }) +
            (doc.status !== 'active' ? chip('decision', doc.status) : '') +
            '<a class="btn" href="' + buildHash('docs', '', { view: 'graph', corpus: corpusUrlValue(docCorpus(doc)), sel: doc.id }) + '">◈ Show in graph</a>' +
          '</div>' +
          '<h1>' + esc(doc.displayTitle) + '</h1>' +
          (doc.readerGoal ? '<p class="doc-goal">' + esc(doc.readerGoal) + '</p>' : '') +
          '<dl class="meta-strip">' +
            '<div><dt>Id</dt><dd><code>' + esc(doc.id) + '</code></dd></div>' +
            (doc.title !== doc.displayTitle
              ? '<div><dt>Registry title</dt><dd>' + esc(doc.title) + '</dd></div>' : '') +
            '<div><dt>Source</dt><dd><code>' + esc(doc.path) + '</code></dd></div>' +
            '<div><dt>Owner</dt><dd>' + esc(doc.owner || 'unrecorded') + '</dd></div>' +
            '<div><dt>Domain</dt><dd>' + esc(doc.domain || 'unrecorded') + '</dd></div>' +
            (doc.updated ? '<div><dt>Updated</dt><dd>' + esc(doc.updated) + '</dd></div>' : '') +
            (doc.version ? '<div><dt>Version</dt><dd>' + esc(doc.version) + '</dd></div>' : '') +
            '<div><dt>Length</dt><dd>' + bytes(doc.bytes) + ' · ' + plural(rendered.headings.length, 'heading') + '</dd></div>' +
          '</dl>' +
        '</header>' +

        ((doc.sourceIssues || []).length
          ? '<p class="source-note" style="margin-top:16px;border-left-color:hsl(var(--h-high) 65% 55%)">' +
            '<strong>Source file defect.</strong> ' +
            doc.sourceIssues.map(esc).join(' ') +
            ' The reading view compensates; the file itself still needs fixing.</p>'
          : '') +

        '<div class="view-switch" role="group" aria-label="Document view">' +
          '<button type="button" data-view="read" aria-pressed="' + (view === 'read') + '">Reading view</button>' +
          '<button type="button" data-view="source" aria-pressed="' + (view === 'source') + '">Markdown source</button>' +
        '</div>' +

        (view === 'read'
          ? '<div class="prose">' + rendered.html + '</div>'
          : '<div>' +
              '<p class="source-note"><strong>Source fallback.</strong> This is the file exactly as it ' +
              'sits on disk, frontmatter included. Use it when the reading view is wrong — the reading ' +
              'view is a projection and this is the ground truth at <code>' + esc(doc.path) + '</code>.</p>' +
              (frontmatter ? '<pre class="source-view" style="margin-bottom:12px">---\n' +
                esc(frontmatter) + '\n---</pre>' : '') +
              '<pre class="source-view">' + esc(MD.splitFrontmatter(doc.body).body.trim()) + '</pre>' +
            '</div>') +

        '<section class="section" style="margin-top:48px">' +
          '<div class="section-head"><h2 style="font-size:var(--step-1)">Relationships</h2>' +
          '<span class="hint">' + plural(outgoing.length + incoming.length, 'link') + ' in the knowledge graph</span></div>' +
          '<div class="relgroups">' +
            '<div class="card relgroup"><h3>This document points to</h3>' + linkList(outgoing) + '</div>' +
            '<div class="card relgroup"><h3>Pointed to by</h3>' + linkList(incoming) + '</div>' +
            '<div class="card relgroup"><h3>Task contracts naming this file</h3>' +
              (relatedTasks.length
                ? '<ul>' + relatedTasks.slice(0, 12).map(function (id) {
                    var t = taskById[id];
                    return '<li><a href="' + buildHash('task', id, {}) + '">' + esc(taskTitle(t)) + '</a> ' +
                      '<span class="rel-kind">— ' + esc(LIFE_LABEL[t.lifecycle]) + '</span></li>';
                  }).join('') + '</ul>' +
                  (relatedTasks.length > 12 ? '<p class="rel-kind"><a href="' +
                    buildHash('tasks', '', { q: doc.path }) + '">' + (relatedTasks.length - 12) +
                    ' more →</a></p>' : '')
                : '<p class="rel-kind">No task contract names this file.</p>') +
            '</div>' +
          '</div>' +
        '</section>' +
      '</div>' +

      '<aside class="toc" aria-label="On this page">' +
        '<h2>On this page</h2>' +
        (toc.length
          ? '<ol>' + toc.map(function (h) {
              return '<li class="lvl-' + h.level + '"><a href="' +
                buildHash('doc', doc.id, state.query, h.anchor) + '" data-anchor="' + esc(h.anchor) + '">' +
                esc(h.text) + '</a></li>';
            }).join('') + '</ol>'
          : '<p class="rel-kind">This document has no sub-headings.</p>') +
      '</aside>' +
      '</div></div>';

    main.querySelectorAll('[data-view]').forEach(function (button) {
      button.addEventListener('click', function () {
        setQuery({ view: button.getAttribute('data-view') === 'source' ? 'source' : '' });
      });
    });

    if (state.anchor) {
      var target = document.getElementById(state.anchor);
      if (target) target.scrollIntoView();
    } else {
      window.scrollTo(0, 0);
    }
    wireScrollSpy();
  }

  var scrollSpy = null;
  function wireScrollSpy() {
    if (scrollSpy) { window.removeEventListener('scroll', scrollSpy); scrollSpy = null; }
    var links = Array.prototype.slice.call(main.querySelectorAll('.toc a[data-anchor]'));
    if (!links.length) return;
    var targets = links.map(function (a) { return document.getElementById(a.getAttribute('data-anchor')); });
    scrollSpy = function () {
      var best = -1;
      for (var i = 0; i < targets.length; i++) {
        if (targets[i] && targets[i].getBoundingClientRect().top < 120) best = i;
      }
      links.forEach(function (a, i) {
        if (i === best) a.setAttribute('aria-current', 'true');
        else a.removeAttribute('aria-current');
      });
    };
    window.addEventListener('scroll', scrollSpy, { passive: true });
    scrollSpy();
  }

  // ------------------------------------------------------------- TASK LIST
  function screenTasks() {
    var q = state.query.q || '';
    var terms = tokens(q);
    var life = state.query.life || '';
    var feature = state.query.feature || '';
    var risk = state.query.risk || '';
    var tool = state.query.tool || '';
    var area = state.query.area || '';
    var sort = state.query.sort || 'id';
    var safeAreas = taskPortfolioAreas();

    function touches(t, key) {
      var areas = presentationAreas(t, 'touched');
      for (var i = 0; i < areas.length; i++) {
        if (areas[i].key === key) return true;
      }
      return false;
    }

    function passes(t, skip) {
      if (life && skip !== 'life' && t.lifecycle !== life) return false;
      if (feature && skip !== 'feature' && taskFeatureKey(t) !== feature) return false;
      if (risk && skip !== 'risk' && taskRisk(t).split(' ')[0] !== risk) return false;
      if (tool && skip !== 'tool' && taskTool(t).split(' ')[0] !== tool) return false;
      if (area && skip !== 'area' && !touches(t, area)) return false;
      return true;
    }

    var pool = DATA.tasks.filter(function (t) { return passes(t); });
    var results = pool;
    if (terms.length) {
      results = pool.map(function (t) { return { t: t, s: scoreTask(t, terms) }; })
        .filter(function (r) { return r.s > 0; })
        .sort(function (a, b) { return b.s - a.s || numberOf(b.t) - numberOf(a.t); })
        .map(function (r) { return r.t; });
    } else if (sort === 'id') {
      results = pool.slice().sort(function (a, b) {
        return numberOf(b) - numberOf(a) || a.id.localeCompare(b.id);
      });
    } else if (sort === 'title') {
      results = pool.slice().sort(function (a, b) { return a.title.localeCompare(b.title); });
    } else if (sort === 'lifecycle') {
      results = pool.slice().sort(function (a, b) {
        return LIFE_ORDER.indexOf(a.lifecycle) - LIFE_ORDER.indexOf(b.lifecycle) ||
          numberOf(b) - numberOf(a);
      });
    }

    function count(field, value, skip) {
      return DATA.tasks.filter(function (t) {
        if (!passes(t, skip)) return false;
        if (field === 'life') return t.lifecycle === value;
        if (field === 'risk') return taskRisk(t).split(' ')[0] === value;
        if (field === 'tool') return taskTool(t).split(' ')[0] === value;
        return false;
      }).length;
    }

    var risks = {};
    var tools = {};
    DATA.tasks.forEach(function (t) {
      var r = taskRisk(t).split(' ')[0];
      if (r) risks[r] = 1;
      var w = taskTool(t).split(' ')[0];
      if (w) tools[w] = 1;
    });

    var featureOptions = DATA.features.slice().sort(function (a, b) {
      return b.count - a.count || a.label.localeCompare(b.label);
    });

    var facets =
      '<div class="facets">' +
        '<div class="facet"><h3>Lifecycle <span class="rel-kind" title="Derived from the folder the contract lives in">(from folder)</span></h3><div class="facet-list">' +
          LIFE_ORDER.filter(function (k) { return DATA.counts.lifecycle[k]; }).map(function (k) {
            return '<button class="facet-btn" type="button" style="--h: var(--h-' + k + ', 220)"' +
              ' data-facet="life" data-value="' + k + '" aria-pressed="' + (life === k) + '">' +
              '<span class="swatch"></span>' + LIFE_LABEL[k] +
              '<span class="n">' + count('life', k, 'life') + '</span></button>';
          }).join('') +
        '</div></div>' +
        '<div class="facet"><h3>Risk</h3><div class="facet-list">' +
          ['high', 'medium', 'low'].filter(function (r) { return risks[r]; }).map(function (r) {
            return '<button class="facet-btn" type="button" style="--h: var(--h-' + r + ', 220)"' +
              ' data-facet="risk" data-value="' + r + '" aria-pressed="' + (risk === r) + '">' +
              '<span class="swatch"></span>' + r +
              '<span class="n">' + count('risk', r, 'risk') + '</span></button>';
          }).join('') +
        '</div></div>' +
        '<div class="facet"><h3>Preferred tool</h3><div class="facet-list">' +
          Object.keys(tools).sort().map(function (w) {
            return '<button class="facet-btn" type="button" data-facet="tool" data-value="' + esc(w) + '"' +
              ' aria-pressed="' + (tool === w) + '">' + esc(w) +
              '<span class="n">' + count('tool', w, 'tool') + '</span></button>';
          }).join('') +
        '</div></div>' +
        '<div class="facet"><h3>Technical footprint</h3>' +
          '<label class="sr-only" for="areasel">Filter by derived technical area</label>' +
          '<select id="areasel" style="width:100%">' +
            '<option value="">Any area (' + safeAreas.length + ')</option>' +
            safeAreas.map(function (a) {
              return '<option value="' + esc(a.key) + '"' + (area === a.key ? ' selected' : '') + '>' +
                esc(a.label) + ' (' + a.taskCount + ')</option>';
            }).join('') +
          '</select>' +
          '<p class="rel-kind" style="margin:8px 0 0;font-size:11.5px">Stable area labels derived for ' +
          'technical navigation; they are not the task’s authored feature scope.</p>' +
        '</div>' +
        '<div class="facet"><h3>Feature</h3>' +
          '<label class="sr-only" for="featsel">Filter by feature</label>' +
          '<select id="featsel" style="width:100%">' +
            '<option value="">All features (' + DATA.features.length + ')</option>' +
            featureOptions.map(function (f) {
              return '<option value="' + esc(f.key) + '"' + (feature === f.key ? ' selected' : '') + '>' +
                esc(f.label) + ' (' + f.count + ')' + (f.variantCount > 1 ? ' ⚠' : '') + '</option>';
            }).join('') +
          '</select>' +
          '<p class="rel-kind" style="margin:8px 0 0;font-size:11.5px">⚠ marks a feature key whose ' +
          'contracts spell the label more than one way. Feature is a display label, not a stable id.</p>' +
        '</div>' +
      '</div>';

    var body;
    if (!results.length) {
      body = emptyState('task contracts', 'reset-tasks');
    } else {
      body = '<div class="card list-card"><div class="rows">' +
        results.slice(0, 400).map(function (t) { return taskRow(t, terms); }).join('') +
        '</div></div>' +
        (results.length > 400 ? '<p class="result-count" style="margin-top:12px">Showing the first 400 of ' +
          results.length + ' — narrow the filters to see the rest.</p>' : '');
    }

    var featureLabel = feature ? (featureByKey[feature] ? featureByKey[feature].label : feature) : '';
    var areaMatch = null;
    for (var ai = 0; ai < safeAreas.length; ai++) {
      if (safeAreas[ai].key === area) areaMatch = safeAreas[ai];
    }
    var areaLabel = areaMatch ? areaMatch.label : area;

    main.innerHTML =
      '<div class="page">' +
        '<div class="section-head"><h2>Task contracts</h2>' +
        '<span class="hint">Every discoverable task contract, across current and historical lifecycle states</span></div>' +
        '<div class="catalog">' + facets +
        '<div>' +
          '<div class="toolbar">' +
            '<div class="grow"><label class="sr-only" for="taskq">Search task contracts</label>' +
            '<input id="taskq" type="search" placeholder="Search ids, titles, feature meaning and relationships…" value="' +
            esc(q) + '" style="width:100%"></div>' +
            '<label class="sr-only" for="tasksort">Sort</label>' +
            '<select id="tasksort"' + (terms.length ? ' disabled title="Sorted by relevance while searching"' : '') + '>' +
              ['id:Newest task id first', 'title:Title A–Z', 'lifecycle:Grouped by lifecycle']
                .map(function (o) {
                  var kv = o.split(':');
                  return '<option value="' + kv[0] + '"' + (sort === kv[0] ? ' selected' : '') + '>' + kv[1] + '</option>';
                }).join('') +
            '</select>' +
            '<span class="result-count" role="status">' + results.length + ' of ' + DATA.tasks.length + '</span>' +
          '</div>' +
          activeFilterBar([
            { key: 'q', label: 'Search', value: q },
            { key: 'life', label: 'Lifecycle', value: life, display: LIFE_LABEL[life] },
            { key: 'feature', label: 'Feature', value: feature, display: featureLabel },
            { key: 'area', label: 'Area', value: area, display: areaLabel },
            { key: 'risk', label: 'Risk', value: risk },
            { key: 'tool', label: 'Tool', value: tool }
          ]) +
          body +
        '</div></div></div>';

    wireCatalog('taskq', 'tasksort');
    var featureSelect = document.getElementById('featsel');
    if (featureSelect) {
      featureSelect.addEventListener('change', function () { setQuery({ feature: featureSelect.value }); });
    }
    var areaSelect = document.getElementById('areasel');
    if (areaSelect) {
      areaSelect.addEventListener('change', function () { setQuery({ area: areaSelect.value }); });
    }
  }

  // ----------------------------------------------------------- TASK DETAIL
  function screenTask() {
    var task = taskById[state.param];
    if (!task) return notFound('task contract', state.param, buildHash('tasks', '', {}));
    var view = ['evidence', 'review', 'source'].indexOf(state.query.view) !== -1 ? state.query.view : 'spec';
    var p = taskPresentation(task);
    var feature = featureByKey[taskFeatureKey(task)];
    var authored = p.state === 'authored';
    var delivery = p.delivery || {
      stage: 'planned',
      label: 'Planned · awaiting execution',
      receiptCount: 0,
      executorReceiptCount: 0,
      qaReceiptCount: 0,
      closed: false,
      accepted: false
    };
    var sourceOnly = p.sourceOnly || {};
    var sourceOnlyCount = ['receiptFieldCount', 'contextFieldCount', 'evidenceFieldCount', 'noteCount']
      .reduce(function (total, key) { return total + (Number(sourceOnly[key]) || 0); }, 0);

    var deliveryView =
      '<section class="delivery-state" data-stage="' + esc(delivery.stage) +
        '" data-state="' + esc(p.state) + '" aria-label="Actual delivery state">' +
        '<header class="delivery-head"><div><p class="eyebrow">Typed progress</p>' +
          '<h2>Actual delivery state</h2>' +
          '<p>Lifecycle is governed separately. Progress uses only typed executor and QA receipt counts.</p></div>' +
          '<span class="delivery-badge">' + esc(delivery.label) + '</span></header>' +
        '<div class="delivery-steps">' +
          '<div class="delivery-step planned ' + (delivery.stage === 'planned' ? 'is-current' : 'is-done') + '">' +
            '<span class="delivery-index">01</span><div><h3>Planned</h3>' +
            (authored
              ? '<p>The human contract defines purpose, scope and ' +
                  plural(p.acceptance.length, 'acceptance check') + '.</p>'
              : '<p>Human task meaning is unavailable for this legacy contract.</p>') +
            '<span class="delivery-evidence">' + esc(LIFE_LABEL[task.lifecycle]) +
              ' lifecycle · ' + (authored ? 'presentation v' + esc(p.schemaVersion) : 'legacy presentation state') +
            '</span></div></div>' +
          '<div class="delivery-step executed ' +
            (delivery.stage === 'planned' ? 'is-next' : (delivery.stage === 'executed' ? 'is-current' : 'is-done')) + '">' +
            '<span class="delivery-index">02</span><div><h3>Executed</h3>' +
            (delivery.executorReceiptCount
              ? '<p>' + plural(delivery.executorReceiptCount, 'typed executor receipt') + ' recorded.</p>' +
                '<span class="delivery-evidence">Details are available in Evidence and Review.</span>'
              : '<p>No executor receipt is represented in the typed presentation.</p>' +
                '<span class="delivery-evidence missing">Awaiting executor evidence</span>') +
            '</div></div>' +
          '<div class="delivery-step reviewed ' + (delivery.stage === 'reviewed' ? 'is-current' : 'is-next') + '">' +
            '<span class="delivery-index">03</span><div><h3>Reviewed</h3>' +
            (delivery.qaReceiptCount
              ? '<p>' + plural(delivery.qaReceiptCount, 'typed QA receipt') + ' recorded.</p>' +
                '<span class="delivery-evidence review-outcome">' +
                  (delivery.accepted ? 'Accepted review recorded' : 'Review recorded') + '</span>'
              : '<p>No QA receipt is represented in the typed presentation.</p>' +
                '<span class="delivery-evidence missing">Awaiting independent review</span>') +
            '</div></div>' +
        '</div>' +
        (sourceOnlyCount
          ? '<div class="delivery-integrity"><strong>Additional detail is available in Source</strong>' +
            '<p>' + plural(sourceOnlyCount, 'source-only semantic field') +
              ' could not safely enter the human presentation. ' +
              window.CPTaskRich.sourceAction(task, 'Open Source') + '</p></div>'
          : '') +
      '</section>';

    function prose(text) {
      if (!text) return '<p class="rel-kind">No authored presentation text is available.</p>';
      return '<div class="spec-prose">' + String(text).split(/\n\s*\n/).map(function (paragraph) {
        return '<p>' + esc(paragraph).replace(/\n/g, '<br>') + '</p>';
      }).join('') + '</div>';
    }

    function humanList(items, emptyText, className) {
      if (!items || !items.length) return '<p class="rel-kind">' + esc(emptyText) + '</p>';
      return '<ul class="' + esc(className || 'limit-list') + '">' + items.map(function (item) {
        return '<li>' + esc(item) + '</li>';
      }).join('') + '</ul>';
    }

    function unavailableField() {
      var unavailable = p.unavailable || {};
      return '<div data-state="legacy-unavailable">' +
        '<p class="truth-empty">' + esc(unavailable.guidance ||
          'This legacy task has no authored human presentation.') + '</p>' +
        '<p>' + window.CPTaskRich.sourceAction(task) + '</p></div>';
    }

    function refList(items, emptyText) {
      if (!items || !items.length) return '<p class="rel-kind">' + esc(emptyText) + '</p>';
      var seen = {};
      items = items.filter(function (item) {
        var key = (typeof item === 'string' ? item : (item.id || item.ref)) || '';
        if (seen[key]) return false;
        seen[key] = 1;
        return true;
      });
      return '<ul>' + items.map(function (item) {
        var id = typeof item === 'string' ? item : item.id;
        var ref = typeof item === 'string' ? item : item.ref;
        var related = id && taskById[id];
        if (!related) {
          var safeRef = /^task_[0-9a-z_]+$/i.test(String(ref || ''))
            ? ref : 'Unresolved task relationship';
          return '<li><span class="unresolved">' + esc(safeRef) + '</span></li>';
        }
        return '<li style="--h: var(--h-' + related.lifecycle + ')">' +
          '<a href="' + buildHash('task', related.id, {}) + '">' + esc(taskTitle(related)) + '</a> ' +
          '<span class="rel-kind">— ' + esc(LIFE_LABEL[related.lifecycle]) + '</span></li>';
      }).join('') + '</ul>';
    }

    var parent = task.rel.parent ? taskById[task.rel.parent] : null;
    var relGroups = [
      { title: 'Depends on', items: task.rel.resolved.dependsOn.concat(task.rel.resolved.blockedBy),
        empty: 'Nothing blocks this contract.' },
      { title: 'Blocks', items: task.rel.blocks, empty: 'No contract waits on this one.' },
      { title: parent ? 'Slice of' : 'Decomposes into',
        items: parent ? [{ ref: parent.id, id: parent.id }] : task.rel.slices,
        empty: 'Standalone contract — no parent and no slices.' },
      { title: 'Parallel-safe with', items: task.rel.resolved.parallelWith,
        empty: 'No parallel-safety recorded.' }
    ];
    var docRefs = task.rel.relatedDocs.map(function (id) { return docById[id]; }).filter(Boolean);
    var footprint = p.technicalFootprint || {};
    var touched = presentationAreas(task, 'touched');
    var offLimits = presentationAreas(task, 'offLimits');
    var sourceOnlyScopeCount =
      (Number(footprint.unmappedTargetCount) || 0) + (Number(footprint.unmappedForbiddenCount) || 0);

    var specView =
      '<div class="task-mission-grid">' +
        '<section class="spec-block" id="purpose" data-state="' + esc(p.state) + '">' +
          '<h2>Purpose <span class="note">why this feature work matters</span></h2>' +
          (authored ? prose(p.purpose) : unavailableField()) +
        '</section>' +
        '<section class="spec-block" id="outcome" data-state="' + esc(p.state) + '">' +
          '<h2>Required outcome <span class="note">what stakeholders should receive</span></h2>' +
          (authored ? prose(p.outcome) : unavailableField()) +
        '</section>' +
      '</div>' +
      '<section class="spec-block" id="scope" data-state="' + esc(p.state) + '">' +
        '<h2>Scope <span class="note">authored feature impact and non-goals</span></h2>' +
        (authored
          ? '<div class="scope-grid">' +
              '<div class="card scope allow"><h3>Feature scope ' +
                '<span class="rel-kind">(' + p.scope.length + ')</span></h3>' +
                humanList(p.scope, 'No feature scope was authored.') +
              '</div>' +
              '<div class="card scope deny"><h3>Non-goals ' +
                '<span class="rel-kind">(' + p.outOfScope.length + ')</span></h3>' +
                humanList(p.outOfScope, 'No non-goals were authored.') +
              '</div>' +
            '</div>'
          : unavailableField()) +
        '<div style="margin-top:20px">' +
          '<h3>Derived technical footprint ' +
            '<span class="rel-kind">' +
              (footprint.provisional ? 'provisional · ' : '') + 'navigation labels, not feature scope</span></h3>' +
          '<div class="scope-grid">' +
            '<div class="card scope allow"><h3>Areas touched ' +
              '<span class="rel-kind">(' + touched.length + ')</span></h3>' +
              areaChips(touched, 'No mapped technical area is available.') +
            '</div>' +
            '<div class="card scope deny"><h3>Protected areas ' +
              '<span class="rel-kind">(' + offLimits.length + ')</span></h3>' +
              areaChips(offLimits, 'No mapped protected area is available.') +
            '</div>' +
          '</div>' +
          (sourceOnlyScopeCount
            ? '<p class="rel-kind" style="margin-top:12px">' +
              plural(sourceOnlyScopeCount, 'additional technical entry') +
              ' remain source-only because no stable area label was available. ' +
              window.CPTaskRich.sourceAction(task, 'Open Source') + '</p>'
            : '') +
        '</div>' +
      '</section>' +
      '<section class="spec-block" id="acceptance" data-state="' + esc(p.state) + '">' +
        '<h2>Acceptance <span class="note">' +
          (authored ? plural(p.acceptance.length, 'check') : 'legacy presentation unavailable') + '</span></h2>' +
        (authored
          ? (p.acceptance.length
            ? '<ol class="checks">' + p.acceptance.map(function (item) {
                return '<li>' + esc(item) + '</li>';
              }).join('') + '</ol>'
            : '<p class="rel-kind">No human acceptance criteria were authored.</p>')
          : unavailableField()) +
      '</section>' +
      '<section class="spec-block" id="relationships">' +
        '<h2>Relationships</h2>' +
        '<div class="relgroups">' +
          relGroups.map(function (group) {
            return '<div class="card relgroup"><h3>' + esc(group.title) + '</h3>' +
              refList(group.items, group.empty) + '</div>';
          }).join('') +
          '<div class="card relgroup"><h3>Control-plane documents named</h3>' +
            (docRefs.length
              ? '<ul>' + docRefs.map(function (doc) {
                  return '<li style="--h: var(--h-' + doc.type + ')"><a href="' +
                    buildHash('doc', doc.id, {}) + '">' + esc(doc.displayTitle) + '</a> ' +
                    '<span class="rel-kind">— ' + esc(TYPE_LABEL[doc.type] || doc.type) + '</span></li>';
                }).join('') + '</ul>'
              : '<p class="rel-kind">This contract names no registered document.</p>') +
          '</div>' +
        '</div>' +
      '</section>';

    var evidenceView = window.CPTaskRich.evidence(task);
    var reviewView = window.CPTaskRich.review(task);
    var sourceView = window.CPTaskRich.source(task);

    main.innerHTML =
      '<div class="page"><div class="reader">' +
      '<div class="reader-main">' +
        '<nav class="breadcrumb" aria-label="Breadcrumb">' +
          '<a href="' + buildHash('home', '', {}) + '">Control plane</a><span class="sep">/</span>' +
          '<a href="' + buildHash('tasks', '', {}) + '">Tasks</a><span class="sep">/</span>' +
          '<a href="' + buildHash('tasks', '', { life: task.lifecycle }) + '">' +
            esc(LIFE_LABEL[task.lifecycle]) + '</a>' +
          (taskFeatureKey(task) ? '<span class="sep">/</span><a href="' +
            buildHash('tasks', '', { feature: taskFeatureKey(task) }) + '">' +
            esc(taskFeatureLabel(task)) + '</a>' : '') +
        '</nav>' +

        '<header class="task-head" data-state="' + esc(p.state) + '">' +
          '<p class="idline">' + esc(task.id) + ' · governed lifecycle</p>' +
          '<h1>' + esc(taskTitle(task)) + '</h1>' +
          '<div class="task-badges">' +
            chip(task.lifecycle, LIFE_LABEL[task.lifecycle],
              { dot: true, title: 'Derived from governed folder state' }) +
            chip(delivery.stage === 'reviewed' ? 'done' :
              (delivery.stage === 'executed' ? 'project-doc' : 'queued'),
              delivery.label, { title: 'Typed delivery state' }) +
            (taskFeatureLabel(task)
              ? '<a href="' + buildHash('tasks', '', { feature: taskFeatureKey(task) }) +
                '" style="text-decoration:none">' +
                chip('project-doc', taskFeatureLabel(task), { title: 'Feature label' }) + '</a>'
              : chip('archived', 'no feature recorded', { plain: true })) +
            (taskRisk(task)
              ? chip(taskRisk(task).split(' ')[0], 'risk: ' + taskRisk(task)) : '') +
            (taskTool(task) ? chip('project-doc', 'executor: ' + taskTool(task), { plain: true }) : '') +
          '</div>' +
          ((p.titleState === 'source-only' || p.featureState === 'source-only')
            ? '<p class="source-note" style="margin-top:12px">One or more task identity labels are ' +
              'available only in Source; this view uses a stable safe label. ' +
              window.CPTaskRich.sourceAction(task, 'Open Source') + '</p>'
            : '') +
          (feature && feature.variantCount > 1
            ? '<p class="source-note" style="margin-top:12px">This feature label is spelled ' +
              feature.variantCount + ' different ways across ' + feature.count + ' contracts. ' +
              'Grouping here is a normalised display key, not a stable feature id.</p>'
            : '') +
        '</header>' +

        deliveryView +

        '<div class="view-switch task-view-switch" role="group" aria-label="Task detail view">' +
          '<button type="button" data-view="spec" aria-pressed="' + (view === 'spec') + '">Spec</button>' +
          '<button type="button" data-view="evidence" aria-pressed="' + (view === 'evidence') + '">Evidence</button>' +
          '<button type="button" data-view="review" aria-pressed="' + (view === 'review') +
            '">Review &amp; Follow-ups</button>' +
          '<button type="button" data-view="source" aria-pressed="' + (view === 'source') + '">Source</button>' +
        '</div>' +

        (view === 'source' ? sourceView :
          (view === 'evidence' ? evidenceView : (view === 'review' ? reviewView : specView))) +
      '</div>' +

      '<aside class="toc" aria-label="On this page">' +
        '<h2>On this page</h2>' +
        (view === 'source'
          ? '<p class="rel-kind">Complete source projection.</p>'
          : '<ol>' + window.CPTaskRich.toc(view, task).map(function (item) {
              return '<li class="lvl-2"><a href="' + buildHash('task', task.id, state.query, item[0]) +
                '" data-anchor="' + item[0] + '">' + item[1] + '</a></li>';
            }).join('') + '</ol>') +
        '<h2 style="margin-top:24px">Task at a glance</h2>' +
        '<dl class="kv" style="font-size:12px">' +
          '<dt>Presentation</dt><dd>' + esc(authored ? 'Authored · schema v' + p.schemaVersion : 'Legacy · unavailable') + '</dd>' +
          '<dt>Risk</dt><dd>' + esc(taskRisk(task) || '—') + '</dd>' +
          '<dt>Executor</dt><dd>' + esc(taskTool(task) || '—') + '</dd>' +
          '<dt>Reviewer</dt><dd>' + esc(taskReviewTool(task) || '—') + '</dd>' +
          '<dt>Lifecycle</dt><dd>' + esc(LIFE_LABEL[task.lifecycle] || task.lifecycle) + '</dd>' +
        '</dl>' +
      '</aside>' +
      '</div></div>';

    main.querySelectorAll('[data-view]').forEach(function (button) {
      button.addEventListener('click', function () {
        var nextView = button.getAttribute('data-view');
        setQuery({ view: nextView === 'spec' ? '' : nextView });
      });
    });
    if (state.anchor) {
      var target = document.getElementById(state.anchor);
      if (target) target.scrollIntoView();
    } else {
      window.scrollTo(0, 0);
    }
    wireScrollSpy();
  }

  // Scope is expressed as authored feature meaning plus a separate technical
  // footprint. Exact source entries remain in Source and Git.
  function areaChips(areas, emptyText) {
    if (!areas.length) return '<p class="rel-kind">' + esc(emptyText) + '</p>';
    return '<ul class="area-list">' + areas.map(function (a) {
      return '<li><a href="' + buildHash('tasks', '', { area: a.key }) + '">' + esc(a.label) + '</a>' +
        (a.count > 1 ? '<span class="n">' + a.count + ' patterns</span>' : '') + '</li>';
    }).join('') + '</ul>';
  }

  function notFound(what, id, back) {
    main.innerHTML = '<div class="page"><div class="card empty">' +
      '<strong>That ' + esc(what) + ' is not in this snapshot.</strong>' +
      '<p><code>' + esc(id) + '</code> was not found. The snapshot was built from the working tree at ' +
      '<code>' + esc(DATA.meta.sourceCommit) + '</code>.</p>' +
      '<p><a href="' + back + '">Back to the catalog</a></p></div></div>';
  }

  // ------------------------------------------------------------------ GRAPH
  var graph = (function () {
    var nodes = null, edges = null, canvas = null, ctx = null, raf = null;
    var view = { x: 0, y: 0, k: 1 };
    var drag = null, panning = null, hover = null, selected = null;
    var colours = {};
    var filters = { prov: '', type: '' };
    var reduceMotion = window.matchMedia &&
      window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    function readColours() {
      var probe = document.createElement('span');
      probe.style.display = 'none';
      document.body.appendChild(probe);
      var out = { types: {} };
      Object.keys(TYPE_LABEL).concat(['queued']).forEach(function (t) {
        probe.style.color = 'hsl(var(--h-' + t + ', 220) 62% var(--node-l))';
        out.types[t] = getComputedStyle(probe).color;
      });
      var style = getComputedStyle(document.documentElement);
      out.edge = style.getPropertyValue('--edge').trim() || '#999';
      out.ink = style.getPropertyValue('--ink').trim() || '#111';
      out.ink3 = style.getPropertyValue('--ink-3').trim() || '#777';
      out.surface = style.getPropertyValue('--surface').trim() || '#fff';
      out.accent = style.getPropertyValue('--accent').trim() || '#26f';
      document.body.removeChild(probe);
      colours = out;
    }

    function init(container, initialSelection, initialFilters) {
      filters = initialFilters;
      // Deterministic starting positions: a golden-angle spiral in registry
      // order. Same snapshot -> same layout, every time, no seeded RNG needed.
      nodes = DATA.graph.nodes.map(function (n, i) {
        var angle = i * 2.399963;
        var radius = 26 * Math.sqrt(i + 1);
        return {
          id: n.id, title: n.title, type: n.type, degree: n.degree, group: n.group,
          corpus: docCorpus(docById[n.id] || n),
          x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, vx: 0, vy: 0
        };
      });
      var index = {};
      nodes.forEach(function (n) { index[n.id] = n; });
      edges = allGraphEdges.map(function (e) {
        return { s: index[e.source], t: index[e.target], type: e.type, prov: e.provenance,
          bridge: isBridgeEdge(e) };
      }).filter(function (e) { return e.s && e.t; });

      canvas = container.querySelector('canvas');
      ctx = canvas.getContext('2d');
      readColours();
      resize();
      for (var i = 0; i < 400; i++) tick(1);
      fit();
      selected = initialSelection && index[initialSelection] ? index[initialSelection] : null;
      if (selected && !visible(selected)) selected = null;
      bind();
      if (!reduceMotion) animate(60);
      else draw();
      window.addEventListener('resize', onResize);
      return { index: index };
    }

    function onResize() { resize(); draw(); }

    function resize() {
      if (!canvas) return;
      var dpr = window.devicePixelRatio || 1;
      var rect = canvas.parentElement.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      canvas.__w = rect.width;
      canvas.__h = rect.height;
    }

    function tick(alpha) {
      var i, j, a, b, dx, dy, d;
      for (i = 0; i < nodes.length; i++) {
        a = nodes[i];
        if (!visible(a)) continue;
        for (j = i + 1; j < nodes.length; j++) {
          b = nodes[j];
          if (!visible(b)) continue;
          dx = b.x - a.x; dy = b.y - a.y;
          d = Math.sqrt(dx * dx + dy * dy) || 0.01;
          var repel = 7200 / (d * d);
          var ux = dx / d, uy = dy / d;
          a.vx -= ux * repel; a.vy -= uy * repel;
          b.vx += ux * repel; b.vy += uy * repel;
        }
      }
      for (i = 0; i < edges.length; i++) {
        if (!visibleEdge(edges[i])) continue;
        a = edges[i].s; b = edges[i].t;
        dx = b.x - a.x; dy = b.y - a.y;
        d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        var force = (d - 165) * 0.013;
        var fx = (dx / d) * force, fy = (dy / d) * force;
        a.vx += fx; a.vy += fy;
        b.vx -= fx; b.vy -= fy;
      }
      for (i = 0; i < nodes.length; i++) {
        a = nodes[i];
        if (!visible(a)) continue;
        a.vx -= a.x * 0.0016;
        a.vy -= a.y * 0.0016;
        if (a === drag) { a.vx = 0; a.vy = 0; continue; }
        a.x += a.vx * alpha; a.y += a.vy * alpha;
        a.vx *= 0.82; a.vy *= 0.82;
      }
    }

    function animate(frames) {
      if (raf) cancelAnimationFrame(raf);
      var left = frames;
      function step() {
        tick(0.85);
        draw();
        left--;
        if (left > 0 || drag) raf = requestAnimationFrame(step);
        else raf = null;
      }
      raf = requestAnimationFrame(step);
    }

    function radius(node) {
      return 4.5 + Math.min(9, Math.sqrt(node.degree) * 2.4) + (node === selected ? 2.5 : 0);
    }

    function bridgeNeighbour(node) {
      if (!filters.bridges || !edges) return false;
      return edges.some(function (edge) {
        if (!edge.bridge) return false;
        if (edge.s === node) return edge.t.corpus === filters.corpus;
        if (edge.t === node) return edge.s.corpus === filters.corpus;
        return false;
      });
    }

    function visible(node) {
      if (filters.corpus && node.corpus !== filters.corpus && !bridgeNeighbour(node)) return false;
      if (filters.type && node.type !== filters.type) return false;
      var doc = docById[node.id];
      if (filters.group && (!doc || doc.group !== filters.group)) return false;
      if (filters.status && (!doc || doc.status !== filters.status)) return false;
      var terms = tokens(filters.q || '');
      if (terms.length && (!doc || scoreDoc(doc, terms) <= 0)) return false;
      return true;
    }
    function visibleEdge(edge) {
      if (filters.prov && edge.prov !== filters.prov) return false;
      if (edge.bridge) {
        if (!filters.bridges) return false;
        if (filters.corpus && edge.s.corpus !== filters.corpus && edge.t.corpus !== filters.corpus) return false;
      } else if (filters.corpus && (edge.s.corpus !== filters.corpus || edge.t.corpus !== filters.corpus)) {
        return false;
      }
      return visible(edge.s) && visible(edge.t);
    }

    function fit() {
      var vis = nodes.filter(visible);
      if (!vis.length) return;
      var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
      vis.forEach(function (n) {
        minX = Math.min(minX, n.x); maxX = Math.max(maxX, n.x);
        minY = Math.min(minY, n.y); maxY = Math.max(maxY, n.y);
      });
      var pad = 70;
      var w = canvas.__w || 800, h = canvas.__h || 600;
      var kx = (w - pad * 2) / Math.max(1, maxX - minX);
      var ky = (h - pad * 2) / Math.max(1, maxY - minY);
      view.k = Math.max(0.25, Math.min(2.2, Math.min(kx, ky)));
      view.x = w / 2 - ((minX + maxX) / 2) * view.k;
      view.y = h / 2 - ((minY + maxY) / 2) * view.k;
    }

    function toScreen(node) {
      return { x: node.x * view.k + view.x, y: node.y * view.k + view.y };
    }

    function draw() {
      if (!ctx) return;
      var w = canvas.__w, h = canvas.__h;
      ctx.clearRect(0, 0, w, h);

      var neighbours = {};
      if (selected) {
        edges.forEach(function (e) {
          if (!visibleEdge(e)) return;
          if (e.s === selected) neighbours[e.t.id] = 1;
          if (e.t === selected) neighbours[e.s.id] = 1;
        });
      }

      edges.forEach(function (e) {
        if (!visibleEdge(e)) return;
        var a = toScreen(e.s), b = toScreen(e.t);
        var live = selected && (e.s === selected || e.t === selected);
        ctx.save();
        ctx.strokeStyle = live ? colours.accent : colours.edge;
        ctx.globalAlpha = selected ? (live ? 1 : 0.22) : 0.72;
        ctx.lineWidth = live ? 1.9 : 1;
        ctx.setLineDash(e.bridge ? [8, 4] : (e.prov === 'inferred' ? [4, 4] : (e.prov === 'structure' ? [1, 3] : [])));
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
        ctx.restore();
      });

      // Nodes first, labels second, so a label never disappears under a node.
      var labelQueue = [];
      nodes.forEach(function (n) {
        if (!visible(n)) return;
        var p = toScreen(n);
        var r = radius(n) * Math.min(1.35, Math.max(0.75, view.k));
        var dim = selected && n !== selected && !neighbours[n.id];
        ctx.save();
        ctx.globalAlpha = dim ? 0.28 : 1;
        ctx.beginPath();
        ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
        ctx.fillStyle = colours.types[n.type] || colours.ink3;
        ctx.fill();
        if (n === selected || n === hover) {
          ctx.lineWidth = 2.5;
          ctx.strokeStyle = colours.accent;
          ctx.stroke();
        } else if (n.degree === 0) {
          ctx.lineWidth = 1;
          ctx.strokeStyle = colours.surface;
          ctx.stroke();
        }
        ctx.restore();
        var priority = n === selected ? 3 : (n === hover ? 3 : (neighbours[n.id] ? 2 : 0));
        if (!priority && !selected && (n.degree >= 4 || view.k > 1.3)) priority = 1;
        if (priority) labelQueue.push({ n: n, p: p, r: r, priority: priority, dim: dim });
      });

      // Occupancy test: the corpus has a dense core, and unmanaged labels there
      // overlap into mush. Higher-priority labels win the space.
      labelQueue.sort(function (a, b) { return b.priority - a.priority || b.n.degree - a.n.degree; });
      var taken = [];
      labelQueue.forEach(function (item) {
        var n = item.n;
        var label = n.title.length > 32 ? n.title.slice(0, 31) + '…' : n.title;
        ctx.font = (item.priority === 3 ? '600 ' : '') + '12px system-ui, sans-serif';
        var width = ctx.measureText(label).width;
        var box = {
          x0: item.p.x - width / 2 - 3, x1: item.p.x + width / 2 + 3,
          y0: item.p.y + item.r + 2, y1: item.p.y + item.r + 18
        };
        for (var i = 0; i < taken.length; i++) {
          var t = taken[i];
          if (box.x0 < t.x1 && box.x1 > t.x0 && box.y0 < t.y1 && box.y1 > t.y0) {
            if (item.priority < 3) return;
          }
        }
        taken.push(box);
        ctx.save();
        ctx.globalAlpha = item.dim ? 0.5 : 1;
        // A soft plate keeps the label legible over edges and neighbours.
        ctx.fillStyle = colours.surface;
        ctx.globalAlpha = item.dim ? 0.35 : 0.82;
        ctx.fillRect(box.x0, box.y0, box.x1 - box.x0, box.y1 - box.y0);
        ctx.globalAlpha = item.dim ? 0.5 : 1;
        ctx.fillStyle = item.priority === 3 ? colours.ink : colours.ink3;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'top';
        ctx.fillText(label, item.p.x, box.y0 + 1);
        ctx.restore();
      });
    }

    function pick(mx, my) {
      var best = null, bestDistance = 18;
      nodes.forEach(function (n) {
        if (!visible(n)) return;
        var p = toScreen(n);
        var d = Math.hypot(p.x - mx, p.y - my);
        if (d < Math.max(radius(n) + 6, bestDistance)) {
          if (!best || d < bestDistance) { best = n; bestDistance = d; }
        }
      });
      return best;
    }

    function localPoint(event) {
      var rect = canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    }

    function bind() {
      canvas.addEventListener('pointerdown', function (event) {
        var p = localPoint(event);
        var hit = pick(p.x, p.y);
        canvas.setPointerCapture(event.pointerId);
        if (hit) {
          drag = hit;
          drag.__moved = false;
          canvas.classList.add('dragging');
        } else {
          panning = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y };
          canvas.classList.add('dragging');
        }
      });
      canvas.addEventListener('pointermove', function (event) {
        var p = localPoint(event);
        if (drag) {
          drag.x = (p.x - view.x) / view.k;
          drag.y = (p.y - view.y) / view.k;
          drag.__moved = true;
          if (!raf) animate(24);
          return;
        }
        if (panning) {
          view.x = panning.vx + (event.clientX - panning.x);
          view.y = panning.vy + (event.clientY - panning.y);
          draw();
          return;
        }
        var hit = pick(p.x, p.y);
        if (hit !== hover) {
          hover = hit;
          canvas.title = hit ? hit.title : '';
          draw();
        }
      });
      function release(event) {
        if (drag) {
          var wasMoved = drag.__moved;
          var node = drag;
          drag = null;
          canvas.classList.remove('dragging');
          if (!wasMoved) select(node.id);
          else animate(30);
          return;
        }
        if (panning) {
          var moved = Math.hypot(event.clientX - panning.x, event.clientY - panning.y);
          panning = null;
          canvas.classList.remove('dragging');
          if (moved < 3) select('');
        }
      }
      canvas.addEventListener('pointerup', release);
      canvas.addEventListener('pointercancel', function () {
        drag = null; panning = null; canvas.classList.remove('dragging');
      });
      canvas.addEventListener('wheel', function (event) {
        event.preventDefault();
        var p = localPoint(event);
        zoomAt(p.x, p.y, event.deltaY < 0 ? 1.12 : 1 / 1.12);
      }, { passive: false });
    }

    function zoomAt(cx, cy, factor) {
      var k = Math.max(0.25, Math.min(3.5, view.k * factor));
      view.x = cx - (cx - view.x) * (k / view.k);
      view.y = cy - (cy - view.y) * (k / view.k);
      view.k = k;
      draw();
    }

    function select(id) {
      // A push, not a replace: moving between notes is real navigation, so the
      // browser Back button walks back through the reader's exploration.
      setQuery({ sel: id || '' });
      selected = null;
      if (id) {
        for (var i = 0; i < nodes.length; i++) if (nodes[i].id === id) selected = nodes[i];
      }
      draw();
      renderGraphReader(id);
    }

    function setSelection(id) {
      selected = null;
      for (var i = 0; i < nodes.length; i++) if (nodes[i].id === id) selected = nodes[i];
      if (selected && !visible(selected)) selected = null;
      draw();
    }

    function setFilters(next) {
      var bridgeTopologyChanged = !!filters.bridges !== !!next.bridges;
      filters = next;
      if (selected && !visible(selected)) selected = null;
      if (bridgeTopologyChanged) {
        for (var i = 0; i < 70; i++) tick(0.72);
        fit();
      }
      draw();
    }

    function graphStats() {
      return {
        nodes: nodes ? nodes.filter(visible).length : 0,
        edges: edges ? edges.filter(visibleEdge).length : 0
      };
    }

    return {
      init: init, draw: draw, fit: function () { fit(); draw(); },
      zoom: function (f) { zoomAt((canvas.__w || 600) / 2, (canvas.__h || 400) / 2, f); },
      recolour: function () { if (ctx) { readColours(); draw(); } },
      setSelection: setSelection, setFilters: setFilters, stats: graphStats,
      teardown: function () {
        if (raf) cancelAnimationFrame(raf);
        raf = null;
        window.removeEventListener('resize', onResize);
        canvas = null; ctx = null; nodes = null; edges = null; selected = null;
      }
    };
  })();
  window.CPGraph = graph;

  function renderGraphReader(id) {
    var host = document.getElementById('graph-reader');
    if (!host) return;
    var doc = docById[id];
    if (!doc) {
      var visibleStats = graph.stats();
      host.innerHTML =
        '<div class="placeholder">' +
          '<p><strong>Select a note.</strong></p>' +
          '<p>Click any node to read it here — the full document, not a summary card. ' +
          'Drag a node to reposition it, drag the background to pan, scroll to zoom.</p>' +
          '<p class="rel-kind">' + visibleStats.nodes + ' documents · ' +
          visibleStats.edges + ' links</p>' +
        '</div>';
      return;
    }
    var rendered = renderMarkdown(doc.body, doc.path, true);
    var links = (edgesByNode[doc.id] || []).filter(function (link) {
      return state.query.bridges === '1' || !isBridgeEdge(link.edge);
    });
    var relatedTasks = tasksByDoc[doc.id] || [];

    host.innerHTML =
      '<div class="graph-reader-head">' +
        '<div class="task-badges">' +
          chip(doc.type, TYPE_LABEL[doc.type] || doc.type, { dot: true }) +
          '<span class="micro rel-kind">' + bytes(doc.bytes) + ' · ' +
            plural(rendered.headings.length, 'heading') + '</span>' +
          '<span style="flex:1"></span>' +
          '<a class="btn" href="' + buildHash('doc', doc.id, {}) + '">Open full page ↗</a>' +
        '</div>' +
        '<h2>' + esc(doc.displayTitle) + '</h2>' +
        '<p class="rel-kind" style="margin:0"><code>' + esc(doc.path) + '</code></p>' +
      '</div>' +
      '<div class="graph-reader-body" id="graph-reader-body">' +
        // Neighbours as a compact strip: enough to see the shape of the
        // neighbourhood without pushing the note's own text below the fold.
        (links.length
          ? '<div class="neighbour-strip">' + links.slice(0, 8).map(function (l) {
              var other = docById[l.other];
              return '<a href="' + docsGraphHash(l.other) + '" ' +
                'style="--h: var(--h-' + esc(other ? other.type : 'archived') + ', 220)" ' +
                'title="' + esc(relationLabel(l.edge.type, l.dir) + ' · ' + l.edge.provenance +
                  (isBridgeEdge(l.edge) ? ' - bridge' : '')) + '" ' +
                'data-select="' + esc(l.other) + '">' +
                esc(other ? other.displayTitle : l.other) + '</a>';
            }).join('') +
            (links.length > 8
              ? '<a href="#relationships-inline" class="more">+' + (links.length - 8) + ' more</a>'
              : '') +
            '</div>'
          : '<p class="rel-kind" style="margin:0 0 14px">No linked documents.</p>') +

        '<div class="prose">' + rendered.html + '</div>' +

        '<section id="relationships-inline" style="margin-top:32px;padding-top:16px;border-top:1px solid var(--line)">' +
          '<h3 style="font-size:var(--step--1);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3);margin:0 0 8px">' +
          plural(links.length, 'link') + ' · full list</h3>' +
          (links.length
            ? '<div class="graph-rel-inline">' + links.map(function (l) {
                var other = docById[l.other];
                return '<a href="' + docsGraphHash(l.other) + '" ' +
                  'style="--h: var(--h-' + esc(other ? other.type : 'archived') + ', 220)" ' +
                  'data-select="' + esc(l.other) + '">' +
                  esc(other ? other.displayTitle : l.other) +
                  '<span class="k">' + esc(relationLabel(l.edge.type, l.dir)) + ' · ' +
                  esc(l.edge.provenance) + (isBridgeEdge(l.edge) ? ' - bridge' : '') + '</span></a>';
              }).join('') + '</div>'
            : '<p class="rel-kind">No linked documents.</p>') +
          (relatedTasks.length
            ? '<p class="rel-kind" style="margin:10px 0 0"><a href="' +
              buildHash('tasks', '', { q: doc.path }) + '">' +
              plural(relatedTasks.length, 'task contract') + ' name this file →</a></p>'
            : '') +
        '</section>' +
      '</div>';

    host.querySelectorAll('[data-select]').forEach(function (a) {
      a.addEventListener('click', function (event) {
        event.preventDefault();
        selectFromReader(a.getAttribute('data-select'));
      });
    });
  }

  function selectFromReader(id) {
    // The hashchange handler re-renders the graph pane in place (see render()'s
    // sameGraph path), so pushing the hash is enough -- no local mutation.
    setQuery({ sel: id });
  }

  function screenGraph() {
    var corpus = currentCorpus();
    var sel = state.query.sel || '';
    var prov = state.query.prov || '';
    var type = state.query.type || '';
    var corpusDocs = DATA.docs.filter(function (doc) { return docCorpus(doc) === corpus; });
    var typeCount = {};
    corpusDocs.forEach(function (doc) { typeCount[doc.type] = (typeCount[doc.type] || 0) + 1; });
    var types = Object.keys(typeCount).sort();

    main.innerHTML =
      '<div class="documents-graph-shell">' + documentSurfaceBar('graph', corpus) +
      '<div class="graph-screen">' +
        '<div class="graph-canvas-wrap" id="graph-wrap">' +
          '<canvas aria-label="Knowledge graph for ' + esc(CORPUS_LABEL[corpus]) + ' documents" role="img"></canvas>' +
          '<div class="graph-controls">' +
            '<div class="panel">' +
              '<button class="icon-btn" type="button" data-graph="fit">Fit</button>' +
              '<button class="icon-btn" type="button" data-graph="in" aria-label="Zoom in">+</button>' +
              '<button class="icon-btn" type="button" data-graph="out" aria-label="Zoom out">-</button>' +
            '</div>' +
            '<div class="panel graph-search-panel">' +
              '<label class="sr-only" for="graph-q">Search graph</label>' +
              '<input id="graph-q" type="search" placeholder="Search this corpus..." value="' + esc(state.query.q || '') + '">' +
            '</div>' +
            '<div class="panel">' +
              '<span class="rel-kind">Links</span>' +
              ['', 'authored', 'structure', 'inferred'].map(function (p) {
                return '<button class="icon-btn" type="button" data-prov="' + p + '" aria-pressed="' +
                  (prov === p) + '">' + (p || 'all') + '</button>';
              }).join('') +
            '</div>' +
            '<div class="panel">' +
              '<label for="graph-type" class="rel-kind">Type</label>' +
              '<select id="graph-type">' +
                '<option value="">All types (' + corpusDocs.length + ')</option>' +
                types.map(function (t) {
                  return '<option value="' + esc(t) + '"' + (type === t ? ' selected' : '') + '>' +
                    esc(TYPE_LABEL[t] || t) + ' (' + typeCount[t] + ')</option>';
                }).join('') +
              '</select>' +
            '</div>' +
          '</div>' +
          '<div class="graph-legend">' +
            '<h3>Document type</h3>' +
            '<div class="legend-types">' + types.map(function (t) {
              return '<span style="--h: var(--h-' + esc(t) + ', 220)"><i></i>' +
                esc(TYPE_LABEL[t] || t) + '</span>';
            }).join('') + '</div>' +
            '<h3>Link provenance</h3>' +
            '<div class="legend-edges">' +
              '<span><i></i>authored - declared in frontmatter</span>' +
              '<span><i class="str"></i>structure - folder containment</span>' +
              '<span><i class="inf"></i>inferred - body names the file</span>' +
              (state.query.bridges === '1' ? '<span><i class="bridge"></i>bridge - labelled cross-corpus relation</span>' : '') +
            '</div>' +
          '</div>' +
        '</div>' +
        '<aside class="graph-reader" id="graph-reader" aria-label="Selected document"></aside>' +
      '</div></div>';

    graph.init(document.getElementById('graph-wrap'), sel, {
      prov: prov, type: type, corpus: corpus, bridges: state.query.bridges === '1',
      q: state.query.q || '', group: state.query.group || '', status: state.query.status || ''
    });
    renderGraphReader(sel);
    wireDocumentSurface();

    main.querySelectorAll('[data-graph]').forEach(function (button) {
      button.addEventListener('click', function () {
        var action = button.getAttribute('data-graph');
        if (action === 'fit') graph.fit();
        if (action === 'in') graph.zoom(1.25);
        if (action === 'out') graph.zoom(1 / 1.25);
      });
    });
    main.querySelectorAll('[data-prov]').forEach(function (button) {
      button.addEventListener('click', function () { setQuery({ prov: button.getAttribute('data-prov') }); });
    });
    var typeSelect = document.getElementById('graph-type');
    if (typeSelect) typeSelect.addEventListener('change', function () { setQuery({ type: typeSelect.value }); });
    var graphSearch = document.getElementById('graph-q');
    if (graphSearch) {
      var timer = null;
      graphSearch.addEventListener('input', function () {
        clearTimeout(timer);
        var value = graphSearch.value;
        timer = setTimeout(function () { setQuery({ q: value }, true); }, 160);
      });
    }
  }

  function screenProject() {
    if (!window.CPProjectUI || !window.CONTROL_PLANE_PROJECT) {
      main.innerHTML = '<div class="page"><div class="card empty"><strong>Project snapshot unavailable.</strong></div></div>';
      return;
    }
    window.CPProjectUI.render(main, state, { setQuery: setQuery, buildHash: buildHash });
  }
  // ------------------------------------------------------------------ render
  var previousScreen = null;
  function render() {
    var next = parseHash();
    if (next.screen === 'graph') {
      next.screen = 'docs';
      next.query.view = 'graph';
      location.replace(buildHash('docs', '', next.query));
      return;
    }
    var hadGraph = !!document.getElementById('graph-wrap');
    var nextIsGraph = next.screen === 'docs' && next.query.view === 'graph';
    var previousCorpus = queryCorpus(state.query.corpus) || currentCorpus();
    var nextCorpus = queryCorpus(next.query.corpus) || previousCorpus;
    var sameGraph = hadGraph && nextIsGraph && previousCorpus === nextCorpus;
    state = next;
    adoptThemeFromUrl(state.query);

    if (sameGraph) {
      graph.setFilters({
        prov: state.query.prov || '', type: state.query.type || '', corpus: currentCorpus(),
        bridges: state.query.bridges === '1', q: state.query.q || '',
        group: state.query.group || '', status: state.query.status || ''
      });
      graph.setSelection(state.query.sel || '');
      renderGraphReader(state.query.sel || '');
      var readerBody = document.getElementById('graph-reader-body');
      if (readerBody) readerBody.scrollTop = 0;
      syncFilterButtons();
      syncDocumentControls();
      syncNav();
      return;
    }
    if (hadGraph) graph.teardown();
    if (previousScreen === 'project' && next.screen !== 'project' && window.CPProjectUI) {
      window.CPProjectUI.teardown();
    }

    if (state.screen === 'project' || state.screen === 'docs' || state.screen === 'tasks') {
      lastQuery[state.screen] = location.hash.slice(1);
    }

    switch (state.screen) {
      case 'project': screenProject(); break;
      case 'docs': screenDocs(); break;
      case 'doc': screenDoc(); break;
      case 'tasks': screenTasks(); break;
      case 'task': screenTask(); break;
      default: screenHome();
    }
    previousScreen = state.screen;
    syncNav();
    if (state.screen !== 'project' && state.screen !== 'doc' && state.screen !== 'task' && !nextIsGraph) {
      window.scrollTo(0, 0);
    }
  }

  function syncFilterButtons() {
    main.querySelectorAll('[data-prov]').forEach(function (button) {
      button.setAttribute('aria-pressed', String((state.query.prov || '') === button.getAttribute('data-prov')));
    });
  }

  function syncDocumentControls() {
    main.querySelectorAll('[data-doc-corpus]').forEach(function (button) {
      button.setAttribute('aria-pressed', String(currentCorpus() === queryCorpus(button.getAttribute('data-doc-corpus'))));
    });
    var selected = documentForSelection(state.query.sel);
    var selectionState = main.querySelector('[data-doc-selection-state]');
    if (selectionState) {
      selectionState.hidden = !selected;
      selectionState.innerHTML = documentSelectionStateMarkup(selected);
    }
    var bridge = main.querySelector('[data-doc-bridges]');
    if (bridge) bridge.checked = state.query.bridges === '1';
    var search = document.getElementById('graph-q');
    if (search && document.activeElement !== search) search.value = state.query.q || '';
  }

  function syncNav() {
    var screenToTab = { project: 'project', home: 'home', docs: 'docs', doc: 'docs', graph: 'docs', tasks: 'tasks', task: 'tasks' };
    var current = screenToTab[state.screen] || 'project';
    document.querySelectorAll('#nav a').forEach(function (a) {
      var screen = a.getAttribute('data-screen');
      if (screen === current) a.setAttribute('aria-current', 'page');
      else a.removeAttribute('aria-current');
      if (lastQuery[screen] && screen !== current) a.setAttribute('href', '#' + lastQuery[screen]);
      else a.setAttribute('href', buildHash(screen, '', {}));
    });
  }

  // global search — routes to the catalog with the most hits
  var globalSearch = document.getElementById('global-q');
  var globalTimer = null;
  globalSearch.addEventListener('input', function () {
    clearTimeout(globalTimer);
    var value = globalSearch.value;
    globalTimer = setTimeout(function () {
      if (!value.trim()) return;
      var terms = tokens(value);
      if (!terms.length) return;
      var docHits = DATA.docs.filter(function (d) { return scoreDoc(d, terms) > 0; }).length;
      var taskHits = DATA.tasks.filter(function (t) { return scoreTask(t, terms) > 0; }).length;
      var target = taskHits > docHits ? 'tasks' : 'docs';
      go(target, '', { q: value }, { replace: state.screen === target });
    }, 260);
  });
  globalSearch.addEventListener('keydown', function (event) {
    if (event.key === 'Enter') {
      clearTimeout(globalTimer);
      var terms = tokens(globalSearch.value);
      if (!terms.length) return;
      var docHits = DATA.docs.filter(function (d) { return scoreDoc(d, terms) > 0; }).length;
      var taskHits = DATA.tasks.filter(function (t) { return scoreTask(t, terms) > 0; }).length;
      go(taskHits > docHits ? 'tasks' : 'docs', '', { q: globalSearch.value });
    }
  });

  // In-page fragment links (heading anchors, "+N more", `[see](#section)` inside
  // a document) must scroll, not navigate: writing a bare `#section` into the
  // hash would look like a route to the router and drop the reader on home.
  document.addEventListener('click', function (event) {
    var anchor = event.target && event.target.closest ? event.target.closest('a[href^="#"]') : null;
    if (!anchor) return;
    var href = anchor.getAttribute('href');
    if (!href || href.indexOf('#/') === 0 || href === '#') return;
    var target = document.getElementById(href.slice(1));
    if (!target) return;
    event.preventDefault();
    target.scrollIntoView();
    if (target.tabIndex < 0) target.setAttribute('tabindex', '-1');
    target.focus({ preventScroll: true });
  });

  document.addEventListener('keydown', function (event) {
    if (event.key === '/' && document.activeElement !== globalSearch &&
        !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) {
      event.preventDefault();
      globalSearch.focus();
      globalSearch.select();
    }
  });

  window.addEventListener('hashchange', render);
  render();
})();
