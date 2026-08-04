/* Project Intelligence for Task 193 synthesis.
 *
 * Claude's canvas language and interaction model are preserved: circular nodes,
 * direct manipulation, pan/zoom, fit controls, restrained labels, and a wide
 * inline reader. The distribution is grouped by the current semantic level.
 */
(function () {
  'use strict';

  var P = window.CONTROL_PLANE_PROJECT;
  var CP = window.CP_DATA;
  var host = null;
  var canvas = null;
  var ctx = null;
  var currentModel = null;
  var selected = null;
  var hover = null;
  var drag = null;
  var pan = null;
  var view = { x: 0, y: 0, k: 1 };
  var colours = {};
  var listeners = [];
  var api = null;
  var routeState = null;

  var nodeById = {};
  var fileByPath = {};
  var nodesByPath = {};
  var nodeCrate = {};
  var nodeModule = {};
  var relationByNode = {};
  var crateInsight = {};

  P.nodes.forEach(function (node) {
    nodeById[node.id] = node;
    nodeCrate[node.id] = node.crate;
    nodeModule[node.id] = node.module || '(root)';
    (nodesByPath[node.path] = nodesByPath[node.path] || []).push(node);
    var insight = crateInsight[node.crate] || (crateInsight[node.crate] = { modules: {}, kinds: {} });
    insight.modules[node.module || '(root)'] = (insight.modules[node.module || '(root)'] || 0) + 1;
    insight.kinds[node.kind] = (insight.kinds[node.kind] || 0) + 1;
  });
  P.files.forEach(function (file) { fileByPath[file.path] = file; });
  P.edges.forEach(function (edge) {
    (relationByNode[edge.source] = relationByNode[edge.source] || []).push(edge);
    (relationByNode[edge.target] = relationByNode[edge.target] || []).push(edge);
  });

  function visibleNodes(view) {
    return view && Array.isArray(view.visible_nodes) ? view.visible_nodes : [];
  }

  function rawModulePath(moduleName) {
    return moduleName === '(root)' ? '' : String(moduleName || '');
  }

  function findVisibleNode(view, identity, label, contextFallbacks) {
    var records = visibleNodes(view);
    var exactIdentity = identity == null ? null : String(identity);
    var exactLabel = label == null ? null : String(label);
    var match = exactIdentity == null ? null : records.find(function (record) {
      return String(record.identity) === exactIdentity;
    });
    if (match) return match;
    (contextFallbacks || []).some(function (expected) {
      var keys = Object.keys(expected || {}).filter(function (key) {
        return expected[key] !== null && expected[key] !== undefined;
      });
      if (!keys.length) return false;
      match = records.find(function (record) {
        var source = record.source_context || {};
        return (exactLabel == null || String(record.label) === exactLabel) &&
          keys.every(function (key) {
            return Object.prototype.hasOwnProperty.call(source, key) &&
              String(source[key]) === String(expected[key]);
          });
      }) || null;
      return !!match;
    });
    return match;
  }

  function presentationGroupKey(record, fallbackIdentity) {
    var group = record && record.presentation_group;
    if (group && Object.prototype.hasOwnProperty.call(group, 'key') &&
        group.key !== null && group.key !== undefined) {
      return String(group.key);
    }
    if (record && record.identity !== null && record.identity !== undefined) {
      return String(record.identity);
    }
    return String(fallbackIdentity);
  }

  function clusterForCrate(value) {
    var raw = String(value || '');
    return P.clusters.find(function (cluster) {
      return String(cluster.id) === raw ||
        String(cluster.unitName || '') === raw ||
        String(cluster.packageId || '') === raw;
    }) || null;
  }

  function crateRouteContext(value) {
    var cluster = clusterForCrate(value);
    return {
      cluster: cluster,
      route: cluster ? String(cluster.id) : String(value || ''),
      rust: cluster ? String(cluster.unitName || cluster.id) : String(value || ''),
      packageId: cluster && cluster.packageId != null ? String(cluster.packageId) : null
    };
  }

  function crateView(context) {
    var views = P.views && Array.isArray(P.views.crates) ? P.views.crates : [];
    var direct = context.packageId == null ? null : views.find(function (view) {
      return String(view.package_id) === context.packageId;
    });
    if (direct) return direct;
    return views.find(function (view) {
      return visibleNodes(view).some(function (record) {
        var source = record.source_context || {};
        return source.unit_name != null &&
          String(source.unit_name) === context.rust;
      });
    }) || null;
  }

  function moduleView(context, moduleName) {
    var views = P.views && Array.isArray(P.views.modules) ? P.views.modules : [];
    var rawModule = rawModulePath(moduleName);
    return views.find(function (view) {
      return String(view.unit_name || '') === context.rust &&
        String(view.module_path || '') === rawModule;
    }) || null;
  }

  function fileView(path) {
    var views = P.views && Array.isArray(P.views.files) ? P.views.files : [];
    return views.find(function (view) {
      return String(view.path || '') === String(path || '');
    }) || null;
  }

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function short(value, length) {
    value = String(value || '');
    return value.length > length ? value.slice(0, length - 1) + '…' : value;
  }

  function bytes(value) {
    if (value < 1024) return value + ' B';
    if (value < 1048576) return (value / 1024).toFixed(value < 10240 ? 1 : 0) + ' KB';
    return (value / 1048576).toFixed(1) + ' MB';
  }

  function count(value, singular, plural) {
    return value.toLocaleString() + ' ' + (value === 1 ? singular : (plural || singular + 's'));
  }

  function topLabels(values, limit) {
    return Object.keys(values || {}).sort(function (a, b) {
      return values[b] - values[a] || a.localeCompare(b);
    }).slice(0, limit).map(function (key) { return key; });
  }


  function authoredText(value) {
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (!value || typeof value !== 'object') return '';
    var nested = value.value || value.purpose || value.description ||
      value.summary || value.authoredSummary;
    return nested === value ? '' : authoredText(nested);
  }

  function authoredCratePurpose(cluster, crate) {
    var maps = [P.cratePurposes, P.crateDescriptions, P.packagePurposes];
    var direct = cluster && (cluster.purpose || cluster.description || cluster.summary);
    var directText = authoredText(direct);
    if (directText) return directText;
    for (var i = 0; i < maps.length; i++) {
      var value = maps[i] && maps[i][crate];
      var text = authoredText(value);
      if (text) return text;
    }
    return '';
  }

  function authoredModuleSummary(crate, moduleName, aggregate) {
    var direct = aggregate && (aggregate.purpose || aggregate.description || aggregate.authoredSummary);
    var directText = authoredText(direct);
    if (directText) return directText;
    var maps = [P.moduleSummaries, P.moduleDescriptions, P.modules];
    var keys = [crate + '::' + moduleName, crate + '|' + moduleName, crate + '/' + moduleName, moduleName];
    for (var i = 0; i < maps.length; i++) {
      var map = maps[i];
      if (!map) continue;
      if (Array.isArray(map)) {
        for (var index = 0; index < map.length; index++) {
          var record = map[index] || {};
          var recordCrate = record.unit_name || record.unitName || record.crate;
          var recordModule = record.module_path || record.modulePath || record.module || '(root)';
          if (recordCrate !== crate || recordModule !== moduleName) continue;
          var authored = record.purpose || record.description || record.summary;
          var authoredValue = authoredText(authored);
          if (authoredValue) return authoredValue;
        }
        continue;
      }
      var value = map[crate] && map[crate][moduleName];
      if (!value) {
        for (var j = 0; j < keys.length && !value; j++) value = map[keys[j]];
      }
      var text = authoredText(value);
      if (text) return text;
    }
    return '';
  }

  function purposeBlock(label, text, missingText) {
    return '<section class="project-purpose' + (text ? '' : ' is-missing') + '">' +
      '<span class="eyebrow">' + esc(label) + '</span>' +
      (text ? '<p>' + esc(text) + '</p>' : '<p><strong>Missing authored summary.</strong> ' +
        esc(missingText) + '</p>') + '</section>';
  }

  function projectMetric(node) {
    var type = node.data.type;
    if (type === 'workspace') return P.counts.files + ' files · ' + P.counts.nodes.toLocaleString() + ' symbols';
    if (type === 'crate') {
      var cluster = node.data.cluster || P.clusters.find(function (item) { return item.id === node.data.crate; });
      return cluster ? cluster.files + ' files · ' + cluster.nodes.toLocaleString() + ' symbols' : count(node.amount, 'symbol');
    }
    if (type === 'module') {
      var summary = node.data.summary || {};
      return Object.keys(summary.files || {}).length + ' files · ' + (summary.nodes || node.amount).toLocaleString() + ' symbols';
    }
    if (type === 'file') {
      var file = node.data.file;
      return file.nodes + ' symbols · ' + file.incoming + ' in / ' + file.outgoing + ' out';
    }
    if (type === 'symbol') {
      var semantic = node.data.node;
      return semantic.kind + ' · ' + semantic.incoming + ' in / ' + semantic.outgoing + ' out';
    }
    return count(node.amount, 'item');
  }

  function projectContext(node) {
    var type = node.data.type;
    if (type === 'workspace') return P.counts.edges.toLocaleString() + ' resolved · ' + P.counts.pending.toLocaleString() + ' pending';
    if (type === 'crate') {
      var insight = crateInsight[node.data.crate] || { modules: {}, kinds: {} };
      var modules = topLabels(insight.modules, 3);
      return modules.length ? 'modules: ' + modules.join(' · ') : 'no indexed modules';
    }
    if (type === 'module') {
      var summary = node.data.summary || {};
      return (summary.pending || 0).toLocaleString() + ' pending references';
    }
    if (type === 'file') {
      var file = node.data.file;
      return file.pending.toLocaleString() + ' pending · ' + bytes(file.size);
    }
    if (type === 'symbol') return short(node.data.node.identity, 42);
    return '';
  }
  function addListener(target, type, fn, options) {
    target.addEventListener(type, fn, options);
    listeners.push(function () { target.removeEventListener(type, fn, options); });
  }

  function query() { return routeState.query || {}; }

  function mode() {
    var value = query().mode || 'hierarchy';
    return ['hierarchy', 'dependency', 'call', 'blast'].indexOf(value) === -1
      ? 'hierarchy' : value;
  }

  function scope() {
    var value = query().scope || 'workspace';
    return ['workspace', 'crate', 'module', 'file'].indexOf(value) === -1
      ? 'workspace' : value;
  }

  function graphNode(id, label, kind, group, data, amount) {
    return {
      id: id, label: label, kind: kind,
      group: group === null || group === undefined ? kind : String(group),
      data: data || {}, amount: amount || 0, x: 0, y: 0
    };
  }

  function aggregateEdges(items, owner) {
    var aggregate = {};
    P.edges.forEach(function (edge) {
      if (mode() === 'call' && edge.kind !== 'call') return;
      var source = owner(edge.source);
      var target = owner(edge.target);
      if (!source || !target || source === target || !items[source] || !items[target]) return;
      var key = source + '\u0000' + target + '\u0000' + edge.kind;
      var bucket = aggregate[key] || (aggregate[key] = {
        source: source, target: target, kind: edge.kind,
        provenance: edge.provenance, confidence: edge.confidence, count: 0
      });
      bucket.count += 1;
      bucket.confidence = Math.min(bucket.confidence, edge.confidence);
      if (bucket.provenance !== edge.provenance) bucket.provenance = 'mixed';
    });
    return Object.keys(aggregate).map(function (key) { return aggregate[key]; });
  }

  function workspaceModel() {
    var view = P.views && P.views.workspace;
    var rootViewNode = findVisibleNode(view, 'workspace', 'Workspace', []);
    var nodes = [graphNode(
      'workspace:project', 'project workspace', 'workspace',
      presentationGroupKey(rootViewNode, 'workspace'),
      { type: 'workspace' }, P.counts.nodes
    )];
    var itemMap = {};
    P.clusters.forEach(function (cluster) {
      var id = 'crate:' + cluster.id;
      var packageId = cluster.packageId == null ? null : String(cluster.packageId);
      var rustCrate = String(cluster.unitName || cluster.id);
      var governedIdentity = packageId == null ? null : 'crate:' + packageId;
      var viewNode = findVisibleNode(
        view,
        governedIdentity,
        cluster.label,
        [
          { package_id: packageId },
          { symbol_namespace: rustCrate }
        ]
      );
      itemMap[id] = true;
      nodes.push(graphNode(id, cluster.label, 'crate',
        presentationGroupKey(viewNode, packageId || governedIdentity || id), {
        type: 'crate', crate: cluster.id, cluster: cluster,
        governedViewNode: viewNode
      }, cluster.nodes));
    });
    var edges;
    if (mode() === 'hierarchy') {
      edges = nodes.slice(1).map(function (node) {
        return { source: 'workspace:project', target: node.id, kind: 'contains',
          provenance: 'filesystem+index', confidence: 100, count: 1 };
      });
    } else {
      edges = aggregateEdges(itemMap, function (id) {
        return nodeCrate[id] ? 'crate:' + nodeCrate[id] : '';
      });
    }
    return { nodes: nodes, edges: edges, title: 'Product workspace', omitted: 0 };
  }

  // Module paths are PATHS, not opaque labels. Listing every distinct one at a crate's level
  // dumped the whole subtree at once -- a real product rendered ~500 fully-qualified siblings with
  // nothing to drill into. This shows one level at a time and collapses a chain that has only one
  // child, so a package whose sole entry is `src/` presents its contents directly instead of
  // making the reader click through a level that carries no choice.
  var MODULE_SEPARATORS = /::|[./]/;

  function moduleSegments(moduleName, crate) {
    var raw = rawModulePath(moduleName);
    if (!raw) return [];
    var parts = raw.split(MODULE_SEPARATORS).filter(function (p) { return p.length; });
    // The crate name usually prefixes its own module paths; it is not a level to descend through.
    var crateParts = String(crate || '').split(MODULE_SEPARATORS).filter(function (p) { return p.length; });
    var i = 0;
    while (i < crateParts.length && i < parts.length && parts[i] === crateParts[i]) i++;
    return parts.slice(i);
  }

  function collapsePassthrough(pathsList) {
    // Drop leading segments every path agrees on: they offer no navigational choice.
    var depth = 0;
    for (;;) {
      var candidate = null;
      for (var i = 0; i < pathsList.length; i++) {
        var segs = pathsList[i];
        if (segs.length <= depth + 1) return depth;   // something terminates here; stop collapsing
        if (candidate === null) candidate = segs[depth];
        else if (segs[depth] !== candidate) return depth;
      }
      if (candidate === null) return depth;
      depth++;
    }
  }

  function crateModel(crate) {
    var context = crateRouteContext(crate);
    var view = crateView(context);
    var modules = {};
    var owned = P.nodes.filter(function (node) { return node.crate === context.rust; });
    var segmentsFor = {};
    owned.forEach(function (node) {
      segmentsFor[node.module || '(root)'] = moduleSegments(node.module || '(root)', context.rust);
    });
    var collapsed = collapsePassthrough(Object.keys(segmentsFor).map(function (k) {
      return segmentsFor[k];
    }));
    owned.forEach(function (node) {
      var full = node.module || '(root)';
      var segs = segmentsFor[full];
      // One level below the collapsed depth; anything terminating at that depth is this level's own.
      var moduleName = segs.length > collapsed ? segs[collapsed] : '(root)';
      var id = 'module:' + context.route + '|' + moduleName;
      var bucket = modules[id] || (modules[id] = {
        id: id, module: moduleName, nodes: 0, files: {}, pending: 0
      });
      bucket.nodes += 1;
      bucket.files[node.path] = true;
      bucket.pending += node.pending;
    });
    var rootId = 'crate:' + context.route;
    var rootIdentity = context.packageId == null ? null : 'crate:' + context.packageId;
    var rootViewNode = findVisibleNode(
      view,
      rootIdentity,
      context.cluster && context.cluster.label,
      [{ package_id: context.packageId }]
    );
    var nodes = [graphNode(
      rootId,
      context.cluster ? context.cluster.label : context.route,
      'crate',
      presentationGroupKey(rootViewNode, context.packageId || rootIdentity || rootId),
      {
        type: 'crate', crate: context.route, cluster: context.cluster,
        governedViewNode: rootViewNode
      },
      Object.keys(modules).reduce(function (sum, key) { return sum + modules[key].nodes; }, 0))];
    var itemMap = {};
    Object.keys(modules).sort().forEach(function (id) {
      var item = modules[id];
      var rawModule = rawModulePath(item.module);
      var governedIdentity = 'module:' + context.rust + ':' + rawModule;
      var viewNode = findVisibleNode(
        view,
        governedIdentity,
        rawModule,
        [{ package_id: context.packageId, unit_name: context.rust }]
      );
      itemMap[id] = true;
      nodes.push(graphNode(id, item.module, 'module',
        presentationGroupKey(viewNode, governedIdentity), {
        type: 'module', crate: context.route, module: item.module, summary: item,
        governedViewNode: viewNode
      }, item.nodes));
    });
    var edges;
    if (mode() === 'hierarchy') {
      edges = nodes.slice(1).map(function (node) {
        return { source: rootId, target: node.id, kind: 'contains',
          provenance: 'index-context', confidence: 100, count: 1 };
      });
    } else {
      edges = aggregateEdges(itemMap, function (id) {
        if (nodeCrate[id] !== context.rust) return '';
        return 'module:' + context.route + '|' + (nodeModule[id] || '(root)');
      });
    }
    return { nodes: nodes, edges: edges,
      title: context.cluster ? context.cluster.label : context.route, omitted: 0 };
  }

  function moduleModel(crate, moduleName) {
    var context = crateRouteContext(crate);
    var view = moduleView(context, moduleName);
    var rawModule = rawModulePath(moduleName);
    var files = P.files.filter(function (file) {
      return file.crate === context.rust && rawModulePath(file.module) === rawModule;
    });
    var rootId = 'module:' + context.route + '|' + moduleName;
    var rootIdentity = 'module:' + context.rust + ':' + rawModule;
    var rootViewNode = findVisibleNode(
      view, rootIdentity, rawModule, [{ unit_name: context.rust }]
    );
    var nodes = [graphNode(rootId, moduleName, 'module',
      presentationGroupKey(rootViewNode, rootIdentity), {
      type: 'module', crate: context.route, module: moduleName,
      governedViewNode: rootViewNode
    }, files.reduce(function (sum, file) { return sum + file.nodes; }, 0))];
    var itemMap = {};
    files.forEach(function (file) {
      var id = 'file:' + file.path;
      var viewNode = findVisibleNode(
        view, id, file.path,
        [{ unit_name: context.rust, module_path: rawModule }]
      );
      itemMap[id] = true;
      nodes.push(graphNode(id, file.name, 'file',
        presentationGroupKey(viewNode, id), {
        type: 'file', file: file, governedViewNode: viewNode
      }, file.nodes));
    });
    var edges;
    if (mode() === 'hierarchy') {
      edges = nodes.slice(1).map(function (node) {
        return { source: rootId, target: node.id, kind: 'contains',
          provenance: 'index-context', confidence: 100, count: 1 };
      });
    } else {
      edges = aggregateEdges(itemMap, function (id) {
        var node = nodeById[id];
        return node && itemMap['file:' + node.path] ? 'file:' + node.path : '';
      });
    }
    return { nodes: nodes, edges: edges,
      title: context.route + ' · ' + moduleName, omitted: 0 };
  }

  function fileModel(path) {
    var file = fileByPath[path] || P.files[0];
    var view = fileView(file.path);
    var symbols = (nodesByPath[file.path] || []).slice();
    var selectedId = query().sel && nodeById[query().sel] ? query().sel : '';
    if (!selectedId && file.path === 'crates/aios-core/src/math.rs') {
      selectedId = P.proofSelection.candidates[0] || '';
    }
    var included = {};
    symbols.forEach(function (node) { included[node.id] = true; });
    var rawEdges = [];
    var omitted = 0;

    if (mode() === 'blast' && selectedId) {
      included = {};
      included[selectedId] = true;
      (relationByNode[selectedId] || []).forEach(function (edge) {
        var other = edge.source === selectedId ? edge.target : edge.source;
        if (Object.keys(included).length < 121) included[other] = true;
        else omitted += 1;
      });
    } else if (mode() !== 'hierarchy') {
      P.edges.forEach(function (edge) {
        if (mode() === 'call' && edge.kind !== 'call') return;
        if (!included[edge.source] && !included[edge.target]) return;
        var other = included[edge.source] ? edge.target : edge.source;
        if (!included[other] && Object.keys(included).length >= 180) {
          omitted += 1;
          return;
        }
        included[other] = true;
      });
    }

    var rootId = 'file:' + file.path;
    var rootViewNode = findVisibleNode(
      view, rootId, file.path,
      [{ unit_name: file.crate, module_path: rawModulePath(file.module) }]
    );
    var nodes = [graphNode(rootId, file.name, 'file',
      presentationGroupKey(rootViewNode, rootId), {
      type: 'file', file: file, governedViewNode: rootViewNode
    }, file.nodes)];
    Object.keys(included).forEach(function (id) {
      var node = nodeById[id];
      if (!node) return;
      var viewNode = findVisibleNode(
        view, id, node.qualifiedName || node.public, [{ path: node.path }]
      );
      nodes.push(graphNode(id, node.public.split('::').pop(), node.kind,
        presentationGroupKey(viewNode, id), {
          type: 'symbol', node: node, governedViewNode: viewNode
        },
        node.incoming + node.outgoing));
    });
    if (mode() === 'hierarchy') {
      rawEdges = nodes.slice(1).filter(function (node) {
        return node.data.node && node.data.node.path === file.path;
      }).map(function (node) {
        return { source: rootId, target: node.id, kind: 'defines',
          provenance: 'parser', confidence: 100, count: 1 };
      });
    } else {
      rawEdges = P.edges.filter(function (edge) {
        if (!included[edge.source] || !included[edge.target]) return false;
        return mode() !== 'call' || edge.kind === 'call';
      });
    }
    return {
      nodes: nodes, edges: rawEdges, title: file.path, omitted: omitted,
      forcedSelection: selectedId
    };
  }

  function buildModel() {
    var currentScope = scope();
    var model;
    if (currentScope === 'crate') {
      model = crateModel(query().crate || P.clusters[0].id);
    } else if (currentScope === 'module') {
      var crate = query().crate || P.clusters[0].id;
      model = moduleModel(crate, query().module || '(root)');
    } else if (currentScope === 'file') {
      model = fileModel(query().file || 'crates/aios-core/src/math.rs');
    } else {
      model = workspaceModel();
    }
    var search = (query().q || '').trim().toLowerCase();
    if (search) {
      var keep = {};
      model.nodes.forEach(function (node, index) {
        if (index === 0 || (node.label + ' ' + node.id).toLowerCase().indexOf(search) !== -1) {
          keep[node.id] = true;
        }
      });
      model.nodes = model.nodes.filter(function (node) { return keep[node.id]; });
      model.edges = model.edges.filter(function (edge) {
        return keep[edge.source] && keep[edge.target];
      });
    }
    layout(model.nodes);
    return model;
  }

  function layout(nodes) {
    var groups = {};
    nodes.forEach(function (node) {
      (groups[node.group] = groups[node.group] || []).push(node);
    });
    var keys = Object.keys(groups).sort();
    var columns = Math.max(1, Math.ceil(Math.sqrt(keys.length)));
    var spacingX = 360;
    var spacingY = 280;
    keys.forEach(function (key, groupIndex) {
      var members = groups[key];
      var gx = (groupIndex % columns - (columns - 1) / 2) * spacingX;
      var gy = (Math.floor(groupIndex / columns) - Math.floor((keys.length - 1) / columns) / 2) * spacingY;
      members.forEach(function (node, index) {
        var angle = index * 2.399963;
        var radius = index === 0 ? 0 : 24 + 19 * Math.sqrt(index);
        node.x = gx + Math.cos(angle) * radius;
        node.y = gy + Math.sin(angle) * radius;
      });
    });
  }

  function readColours() {
    var style = getComputedStyle(document.documentElement);
    colours = {
      edge: style.getPropertyValue('--edge').trim() || '#999',
      ink: style.getPropertyValue('--ink').trim() || '#111',
      ink3: style.getPropertyValue('--ink-3').trim() || '#777',
      surface: style.getPropertyValue('--surface').trim() || '#fff',
      sunken: style.getPropertyValue('--surface-sunken').trim() || '#f4f4f4',
      accent: style.getPropertyValue('--accent').trim() || '#26f',
      nodeLight: style.getPropertyValue('--node-l').trim() || '48%'
    };
  }

  function colourFor(node) {
    var hues = {
      workspace: 215, crate: 265, module: 190, file: 37, function: 214,
      method: 285, struct: 156, enum: 22, trait: 332, constant: 48,
      static: 48, type_alias: 176, macro: 9
    };
    return 'hsl(' + (hues[node.kind] || 220) + ' 62% ' + colours.nodeLight + ')';
  }

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

  function fit() {
    if (!currentModel || !currentModel.nodes.length) return;
    var xs = currentModel.nodes.map(function (node) { return node.x; });
    var ys = currentModel.nodes.map(function (node) { return node.y; });
    var minX = Math.min.apply(Math, xs), maxX = Math.max.apply(Math, xs);
    var minY = Math.min.apply(Math, ys), maxY = Math.max.apply(Math, ys);
    var w = canvas.__w || 800, h = canvas.__h || 600, pad = 90;
    view.k = Math.max(.16, Math.min(2.1, Math.min(
      (w - pad * 2) / Math.max(120, maxX - minX),
      (h - pad * 2) / Math.max(120, maxY - minY)
    )));
    view.x = w / 2 - ((minX + maxX) / 2) * view.k;
    view.y = h / 2 - ((minY + maxY) / 2) * view.k;
  }

  function point(node) {
    return { x: node.x * view.k + view.x, y: node.y * view.k + view.y };
  }

  function radius(node) {
    return 5 + Math.min(13, Math.sqrt(Math.max(1, node.amount)) * .72)
      + (selected && selected.id === node.id ? 2.5 : 0);
  }

  function draw() {
    if (!ctx || !currentModel) return;
    var w = canvas.__w || 1, h = canvas.__h || 1;
    ctx.clearRect(0, 0, w, h);
    var index = {};
    currentModel.nodes.forEach(function (node) { index[node.id] = node; });
    var neighbours = {};
    if (selected) {
      currentModel.edges.forEach(function (edge) {
        if (edge.source === selected.id) neighbours[edge.target] = true;
        if (edge.target === selected.id) neighbours[edge.source] = true;
      });
    }

    currentModel.edges.forEach(function (edge) {
      var source = index[edge.source], target = index[edge.target];
      if (!source || !target) return;
      var a = point(source), b = point(target);
      var active = selected && (edge.source === selected.id || edge.target === selected.id);
      ctx.save();
      ctx.strokeStyle = active ? colours.accent : colours.edge;
      ctx.globalAlpha = selected ? (active ? .95 : .18) : .54;
      ctx.lineWidth = active ? 2 : Math.min(2.5, 1 + Math.log10(edge.count || 1) * .45);
      if (edge.kind === 'contains' || edge.kind === 'defines') ctx.setLineDash([1, 4]);
      else if (edge.provenance === 'mixed') ctx.setLineDash([5, 4]);
      ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
      ctx.restore();
    });

    var labels = [];
    currentModel.nodes.forEach(function (node) {
      var p = point(node), r = radius(node) * Math.max(.7, Math.min(1.25, view.k));
      var dim = selected && selected.id !== node.id && !neighbours[node.id];
      ctx.save();
      ctx.globalAlpha = dim ? .25 : 1;
      ctx.beginPath(); ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fillStyle = colourFor(node); ctx.fill();
      if ((selected && selected.id === node.id) || (hover && hover.id === node.id)) {
        ctx.lineWidth = 2.5; ctx.strokeStyle = colours.accent; ctx.stroke();
      } else if (node.kind === 'workspace' || node.kind === 'crate') {
        ctx.lineWidth = 2; ctx.strokeStyle = colours.surface; ctx.stroke();
      }
      ctx.restore();
      var priority = (selected && selected.id === node.id) || (hover && hover.id === node.id)
        ? 3 : (neighbours[node.id] ? 2 : (node.kind === 'workspace' || node.kind === 'crate' ? 1 : 0));
      if (!selected && (currentModel.nodes.length < 45 || node.amount > 12)) priority = Math.max(priority, 1);
      if (priority) labels.push({ node: node, p: p, r: r, priority: priority, dim: dim });
    });

    labels.sort(function (a, b) { return b.priority - a.priority || b.node.amount - a.node.amount; });
    var occupied = [];
    labels.forEach(function (item) {
      var label = short(item.node.label, 30);
      var metric = short(projectMetric(item.node), 36);
      var context = short(projectContext(item.node), 42);
      ctx.font = (item.priority === 3 ? '650 ' : '600 ') + '12px system-ui, sans-serif';
      var labelWidth = ctx.measureText(label).width;
      ctx.font = '10.5px system-ui, sans-serif';
      var metricWidth = ctx.measureText(metric).width;
      var contextWidth = context ? ctx.measureText(context).width : 0;
      var width = Math.min(128, Math.max(82, labelWidth, metricWidth, contextWidth) + 12);
      var height = context ? 46 : 33;
      var box = { x0: item.p.x - width / 2, x1: item.p.x + width / 2,
        y0: item.p.y + item.r + 3, y1: item.p.y + item.r + 3 + height };
      for (var i = 0; i < occupied.length; i += 1) {
        var other = occupied[i];
        if (box.x0 < other.x1 && box.x1 > other.x0 && box.y0 < other.y1 && box.y1 > other.y0) {
          if (item.node.data.type === 'symbol' && item.priority < 2) return;
        }
      }
      occupied.push(box);
      ctx.save();
      ctx.globalAlpha = item.dim ? .3 : .9;
      ctx.fillStyle = colours.surface;
      ctx.fillRect(box.x0, box.y0, box.x1 - box.x0, box.y1 - box.y0);
      ctx.strokeStyle = colours.edge;
      ctx.globalAlpha = item.dim ? .15 : .34;
      ctx.strokeRect(box.x0 + .5, box.y0 + .5, box.x1 - box.x0 - 1, box.y1 - box.y0 - 1);
      ctx.globalAlpha = item.dim ? .4 : 1;
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      ctx.font = (item.priority === 3 ? '650 ' : '600 ') + '12px system-ui, sans-serif';
      ctx.fillStyle = item.priority === 3 ? colours.ink : colours.ink;
      ctx.fillText(label, item.p.x, box.y0 + 3, width - 8);
      ctx.font = '10.5px system-ui, sans-serif';
      ctx.fillStyle = colours.ink3;
      ctx.fillText(metric, item.p.x, box.y0 + 18, width - 8);
      if (context) {
        ctx.font = '9.5px system-ui, sans-serif';
        ctx.fillText(context, item.p.x, box.y0 + 32, width - 8);
      }
      ctx.restore();
    });
  }

  function pick(x, y) {
    var found = null, distance = 22;
    currentModel.nodes.forEach(function (node) {
      var p = point(node);
      var next = Math.hypot(p.x - x, p.y - y);
      if (next < Math.max(distance, radius(node) + 7) && (!found || next < distance)) {
        found = node; distance = next;
      }
    });
    return found;
  }

  function local(event) {
    var rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function bindCanvas() {
    addListener(canvas, 'pointerdown', function (event) {
      var p = local(event), hit = pick(p.x, p.y);
      canvas.setPointerCapture(event.pointerId);
      if (hit) {
        drag = { node: hit, moved: false };
      } else {
        pan = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y };
      }
      canvas.classList.add('dragging');
    });
    addListener(canvas, 'pointermove', function (event) {
      var p = local(event);
      if (drag) {
        drag.node.x = (p.x - view.x) / view.k;
        drag.node.y = (p.y - view.y) / view.k;
        drag.moved = true; draw(); return;
      }
      if (pan) {
        view.x = pan.vx + event.clientX - pan.x;
        view.y = pan.vy + event.clientY - pan.y;
        draw(); return;
      }
      hover = pick(p.x, p.y);
      canvas.title = hover ? hover.label : '';
      draw();
    });
    function release() {
      if (drag) {
        var item = drag; drag = null;
        canvas.classList.remove('dragging');
        if (!item.moved) api.setQuery({ sel: item.node.id });
        return;
      }
      pan = null; canvas.classList.remove('dragging');
    }
    addListener(canvas, 'pointerup', release);
    addListener(canvas, 'pointercancel', release);
    addListener(canvas, 'wheel', function (event) {
      event.preventDefault();
      var p = local(event);
      var factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      var next = Math.max(.15, Math.min(4, view.k * factor));
      view.x = p.x - (p.x - view.x) * next / view.k;
      view.y = p.y - (p.y - view.y) * next / view.k;
      view.k = next; draw();
    }, { passive: false });
  }

  function selectedGraphNode() {
    var id = query().sel || currentModel.forcedSelection || currentModel.nodes[0].id;
    for (var i = 0; i < currentModel.nodes.length; i += 1) {
      if (currentModel.nodes[i].id === id) return currentModel.nodes[i];
    }
    return currentModel.nodes[0];
  }

  function breadcrumb() {
    var parts = [
      '<button type="button" data-scope="workspace">project</button>'
    ];
    if (scope() !== 'workspace') {
      parts.push('<span>/</span><button type="button" data-scope="crate" data-crate="' +
        esc(query().crate || '') + '">' + esc(query().crate || '') + '</button>');
    }
    if (scope() === 'module' || scope() === 'file') {
      var file = scope() === 'file' ? fileByPath[query().file] : null;
      var moduleName = query().module || (file && (file.module || '(root)')) || '';
      parts.push('<span>/</span><button type="button" data-scope="module" data-crate="' +
        esc(query().crate || (file && file.crate) || '') + '" data-module="' +
        esc(moduleName) + '">' + esc(moduleName) + '</button>');
    }
    if (scope() === 'file') {
      parts.push('<span>/</span><strong>' + esc((fileByPath[query().file] || {}).name || '') + '</strong>');
    }
    return '<div class="project-crumb">' + parts.join('') + '</div>';
  }

  function hierarchyNav() {
    var currentScope = scope();
    var file = currentScope === 'file' ? fileByPath[query().file] : null;
    var currentCrate = query().crate || (file && file.crate) || '';
    var currentModule = query().module || (file && (file.module || '(root)')) || '';
    var up = '';
    if (currentScope === 'crate') {
      up = '<button class="project-up" type="button" data-scope="workspace">← All packages</button>';
    } else if (currentScope === 'module') {
      up = '<button class="project-up" type="button" data-scope="crate" data-crate="' +
        esc(currentCrate) + '">← Up to ' + esc(currentCrate) + '</button>';
    } else if (currentScope === 'file') {
      up = '<button class="project-up" type="button" data-scope="module" data-crate="' +
        esc(currentCrate) + '" data-module="' + esc(currentModule) + '">← Up to ' +
        esc(currentModule) + '</button>';
    } else {
      up = '<span class="project-up disabled">Top level</span>';
    }
    return '<div class="project-hierarchy-nav"><span class="hierarchy-label">Hierarchy</span>' +
      up + breadcrumb() + '</div>';
  }

  function relationRows(node) {
    if (!node || node.data.type !== 'symbol') return '';
    var edges = relationByNode[node.id] || [];
    if (!edges.length) return '<p class="rel-kind">No resolved relation touches this symbol.</p>';
    return '<div class="project-relations">' + edges.slice(0, 18).map(function (edge) {
      var outgoing = edge.source === node.id;
      var other = nodeById[outgoing ? edge.target : edge.source];
      if (!other) return '';
      return '<button type="button" data-open-symbol="' + esc(other.id) + '" data-file="' +
        esc(other.path) + '"><span class="direction">' + (outgoing ? 'out' : 'in') + '</span>' +
        '<span class="relation-name">' + esc(short(other.public, 62)) + '</span>' +
        '<span class="relation-meta">' + esc(edge.kind) + ' · ' + esc(edge.provenance) +
        ' · ' + edge.confidence + '%</span></button>';
    }).join('') + (edges.length > 18
      ? '<p class="rel-kind">+' + (edges.length - 18) + ' more resolved relations in the exact Agent View.</p>' : '') +
      '</div>';
  }

  function sourceBlock(file, node) {
    if (!file || !file.source) return '<p class="rel-kind">Source text is unavailable.</p>';
    var lines = file.source.split(/\r?\n/);
    var start = node ? Math.max(0, node.row - 6) : 0;
    var end = node ? Math.min(lines.length, Math.max(node.endRow + 7, node.row + 12)) : Math.min(36, lines.length);
    var body = lines.slice(start, end).map(function (line, index) {
      return String(start + index + 1).padStart(4, ' ') + '  ' + line;
    }).join('\n');
    return '<pre class="project-source"><code>' + esc(body) + '</code></pre>' +
      (file.sourceTruncated ? '<p class="rel-kind">Snapshot source is truncated at 48,000 characters.</p>' : '');
  }

  function matchGlob(pattern, path) {
    pattern = String(pattern || '').trim().replace(/^["']|["']$/g, '');
    if (!pattern || pattern.indexOf('**') === -1 && pattern.indexOf('*') === -1) {
      return pattern === path || pattern.indexOf(path) !== -1;
    }
    var escaped = pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&')
      .replace(/\*\*/g, '\u0000').replace(/\*/g, '[^/]*').replace(/\u0000/g, '.*');
    try { return new RegExp('^' + escaped + '$').test(path); } catch (_) { return false; }
  }

  function crossLinks(file, node) {
    if (!file) return '';
    var tasks = CP.tasks.filter(function (task) {
      return task.spec.targetFiles.some(function (pattern) { return matchGlob(pattern, file.path); });
    }).slice(0, 7);
    var docNeedles = [file.path, file.crate, node && node.public].filter(Boolean);
    var docs = CP.docs.filter(function (doc) {
      return docNeedles.some(function (needle) { return needle.length > 4 && doc.body.indexOf(needle) !== -1; });
    }).slice(0, 6);
    return '<section class="project-crosslinks"><h3>Connected control-plane context</h3>' +
      '<div class="crosslink-grid"><div><h4>Task scope matches</h4>' +
      (tasks.length ? tasks.map(function (task) {
        return '<a href="' + api.buildHash('task', task.id, {}) + '">' + esc(task.title) +
          '<small>declared target scope · ' + esc(task.lifecycleLabel) + '</small></a>';
      }).join('') : '<p class="rel-kind">No task target pattern matches this file.</p>') +
      '</div><div><h4>Documents naming this context</h4>' +
      (docs.length ? docs.map(function (doc) {
        return '<a href="' + api.buildHash('doc', doc.id, {}) + '">' + esc(doc.displayTitle) +
          '<small>literal source mention · ' + esc(doc.type) + '</small></a>';
      }).join('') : '<p class="rel-kind">No registered document literally names this context.</p>') +
      '</div></div></section>';
  }

  function contextBundle(node, file) {
    var relations = [];
    if (node && node.data.type === 'symbol') {
      (relationByNode[node.id] || []).forEach(function (edge) {
        var outgoing = edge.source === node.id;
        var other = nodeById[outgoing ? edge.target : edge.source];
        if (!other) return;
        relations.push({
          direction: outgoing ? 'outbound' : 'inbound',
          kind: edge.kind,
          provenance: edge.provenance,
          confidence: edge.confidence,
          target: { public: other.public, identity: other.identity, path: other.path,
            row: other.row, kind: other.kind }
        });
      });
    }
    return {
      selection: node && node.data.type === 'symbol' ? node.data.node : {
        kind: node ? node.data.type : 'workspace',
        id: node ? node.id : 'workspace:project'
      },
      source: file ? {
        path: file.path, sha256: file.sha256, bytes: file.size,
        source_truncated: file.sourceTruncated
      } : null,
      resolved_relations: relations,
      pending_sample: file ? file.pendingSample : [],
      provenance: {
        source_commit: P.meta.sourceCommit,
        index_sha256: P.meta.indexSha256,
        schema_version: P.meta.schemaVersion,
        node_identity_version: P.meta.nodeIdentityVersion,
        resolution_version: P.meta.resolutionVersion
      },
      ranking: P.agentContext.ranking,
      limits: P.agentContext.limits,
      omitted_or_unresolved: {
        file_pending_total: file ? file.pending : 0,
        relation_display_limit: 18,
        graph_omitted_for_density: currentModel.omitted,
        unresolved_items_are_not_edges: true
      }
    };
  }

  function proofOutput(node) {
    if (!node || node.data.type !== 'symbol') return null;
    var identity = node.data.node.identity;
    if (identity === P.agentContext.queries.vectorExplore.query) {
      return P.agentContext.queries.vectorExplore;
    }
    if (identity === P.agentContext.queries.scalarExplore.query) {
      return P.agentContext.queries.scalarExplore;
    }
    return null;
  }

  function exactOutput(node) {
    var output = proofOutput(node);
    return output && output.state === 'ready' && output.exact === true &&
      output.nonfabricated === true && output.exitCode === 0
      ? output
      : null;
  }

  function readerHtml(node) {
    var type = node ? node.data.type : 'workspace';
    var semantic = type === 'symbol' ? node.data.node : null;
    var file = semantic ? fileByPath[semantic.path] : (type === 'file' ? node.data.file : null);
    var title = node ? node.label : 'Project workspace';
    var subtitle = type;
    var primary = '';
    var actions = '';

    if (type === 'workspace') {
      title = 'Product workspace';
      subtitle = 'repository hierarchy · semantic index';
      primary = '<p>The graph is the project tree: select a package, then progressively expand ' +
        'module, file, and symbol levels without leaving this surface.</p>';
    } else if (type === 'crate') {
      var cluster = node.data.cluster || P.clusters.filter(function (item) {
        return item.id === node.data.crate;
      })[0];
      subtitle = 'crate cluster';
      var cratePurpose = authoredCratePurpose(cluster, node.data.crate);
      primary = purposeBlock('Purpose', cratePurpose,
        'No package purpose was exported from the source metadata.') + '<dl class="project-facts"><dt>Files</dt><dd>' + (cluster ? cluster.files : '—') +
        '</dd><dt>Symbols</dt><dd>' + (cluster ? cluster.nodes.toLocaleString() : '—') +
        '</dd><dt>Resolved edge touches</dt><dd>' +
        (cluster ? cluster.resolvedEdges.toLocaleString() : '—') + '</dd><dt>Pending</dt><dd>' +
        (cluster ? cluster.pending.toLocaleString() : '—') +
        '</dd><dt>Rust semantic identity</dt><dd><code>' +
        esc(cluster ? (cluster.unitName || cluster.id) : node.data.crate) + '</code></dd></dl>';
      actions = '<button class="btn primary" type="button" data-expand-crate="' +
        esc(node.data.crate) + '">Expand modules</button>';
    } else if (type === 'module') {
      subtitle = node.data.crate + ' · module';
      var summary = node.data.summary || {};
      var moduleSummary = authoredModuleSummary(node.data.crate, node.data.module, summary);
      primary = purposeBlock('Module summary', moduleSummary,
        'No authored module summary was exported; counts below are index facts only.') + '<dl class="project-facts"><dt>Files</dt><dd>' +
        Object.keys(summary.files || {}).length + '</dd><dt>Symbols</dt><dd>' +
        (summary.nodes || node.amount).toLocaleString() + '</dd><dt>Pending</dt><dd>' +
        (summary.pending || 0).toLocaleString() + '</dd></dl>';
      actions = '<button class="btn primary" type="button" data-expand-module="' +
        esc(node.data.module) + '" data-crate="' + esc(node.data.crate) + '">Expand files</button>';
    } else if (type === 'file') {
      file = node.data.file;
      title = file.name;
      subtitle = file.crate + ' · ' + file.module;
      primary = '<p class="identity-path">' + esc(file.path) + '</p><dl class="project-facts">' +
        '<dt>Language</dt><dd>Rust</dd><dt>Symbols</dt><dd>' + file.nodes +
        '</dd><dt>Inbound</dt><dd>' + file.incoming + '</dd><dt>Outbound</dt><dd>' +
        file.outgoing + '</dd><dt>Pending</dt><dd>' + file.pending.toLocaleString() +
        '</dd><dt>Source</dt><dd>' + bytes(file.size) + '</dd></dl>';
      actions = '<button class="btn primary" type="button" data-expand-file="' +
        esc(file.path) + '" data-crate="' + esc(file.crate) + '">Expand symbols</button>';
    } else if (semantic) {
      title = semantic.public.split('::').pop();
      subtitle = semantic.kind + ' · ' + semantic.crate;
      primary = '<p class="public-name"><span>Public name</span><code>' +
        esc(semantic.public) + '</code></p><p class="semantic-owner"><span>Semantic owner</span><code>' +
        esc(semantic.identity) + '</code></p><p class="identity-path">' + esc(semantic.path) +
        ':' + (semantic.row + 1) + ':' + (semantic.column + 1) + '</p>' +
        '<dl class="project-facts"><dt>Inbound</dt><dd>' + semantic.incoming +
        '</dd><dt>Outbound</dt><dd>' + semantic.outgoing + '</dd><dt>Pending owned</dt><dd>' +
        semantic.pending + '</dd><dt>Identity</dt><dd title="' + esc(semantic.id) + '">' +
        esc(semantic.id.slice(0, 12)) + '…</dd></dl>';
      actions = '<button class="btn" type="button" data-expand-file="' +
        esc(semantic.path) + '" data-crate="' + esc(semantic.crate) + '">Whole file</button>';
    }

    var proof = proofOutput(node);
    var raw = exactOutput(node);
    var bundle = contextBundle(node, file);
    var bundleState = String(P.agentResultBundles && P.agentResultBundles.state ||
      P.agentContext && P.agentContext.state || 'unavailable');
    var guidance = String(P.agentResultBundles && P.agentResultBundles.guidance ||
      P.agentContext && P.agentContext.contract || 'No Agent View guidance was exported.');
    var truthLabel = raw ? 'exact command output' : 'derived context';
    var agentBody = raw
      ? '<p class="agent-contract">Exact, non-fabricated ai-impact stdout for this semantic identity.</p>' +
        '<pre class="agent-output"><code>' + esc(raw.stdout) + '</code></pre>'
      : (
        proof
          ? '<p class="agent-contract"><strong>Exact query state: ' +
            esc(proof.state || bundleState) + '.</strong> No command output is presented as exact. ' +
            esc(guidance) + '</p>' +
            ((proof.stderr || proof.stdout)
              ? '<details><summary>Captured query diagnostic</summary><pre class="agent-output"><code>' +
                esc(proof.stderr || proof.stdout) + '</code></pre></details>'
              : '')
          : '<p class="agent-contract"><strong>Command proof bundle: ' + esc(bundleState) +
            '.</strong> Exact command output is available only when the selected semantic identity ' +
            'matches a ready governed proof query. ' + esc(guidance) + '</p>'
      ) +
      '<p class="agent-contract"><strong>Derived, non-fabricated context.</strong> ' +
        'The bundle below is assembled from persisted index rows; it is not ai-impact command output.</p>' +
      '<pre class="agent-output"><code>' + esc(JSON.stringify(bundle, null, 2)) + '</code></pre>';

    var ambiguity = semantic && semantic.public === P.proofSelection.public
      ? '<section class="ambiguity-proof"><div><span class="eyebrow">Public ambiguity retained</span>' +
        '<strong>2 selectable definitions</strong><p>The public lookup stays useful, while the semantic ' +
        'owner prevents last-writer-wins identity.</p></div>' +
        P.proofSelection.candidates.map(function (id) {
          var candidate = nodeById[id];
          return '<button type="button" data-open-symbol="' + esc(id) + '" data-file="' +
            esc(candidate.path) + '" aria-current="' + (semantic.id === id) + '">' +
            esc(candidate.identity) + '</button>';
        }).join('') + '</section>' : '';

    return '<div class="graph-reader-head project-reader-head"><span class="eyebrow">' +
      esc(subtitle) + '</span><h2>' + esc(title) + '</h2>' + primary +
      (actions ? '<div class="reader-actions">' + actions + '</div>' : '') + '</div>' +
      '<div class="graph-reader-body project-reader-body">' +
        ambiguity +
        (semantic ? '<section class="project-reader-section"><h3>Resolved relationships</h3>' +
          relationRows(node) + '</section>' : '') +
        (file ? '<section class="project-reader-section"><h3>Source context</h3>' +
          sourceBlock(file, semantic) + '</section>' : '') +
        '<section class="project-reader-section agent-view" id="agent-view"><div class="section-title">' +
          '<div><span class="eyebrow">Transparent injection</span><h3>Agent View / Context</h3></div>' +
          '<span class="truth-pill">' + truthLabel + '</span></div>' + agentBody +
          '<details><summary>Ranking, limits, and omissions</summary><pre class="agent-output"><code>' +
          esc(JSON.stringify({
            ranking: P.agentContext.ranking,
            limits: P.agentContext.limits,
            model_omitted_for_density: currentModel.omitted,
            unresolved_is_not_edge: true
          }, null, 2)) + '</code></pre></details></section>' +
        crossLinks(file, semantic) +
      '</div>';
  }

  function renderReader() {
    var readerNode = selectedGraphNode();
    selected = readerNode.data.type === 'workspace' ? null : readerNode;
    var reader = host.querySelector('#project-reader');
    if (reader) reader.innerHTML = readerHtml(readerNode);
    draw();
    bindReader();
    if (query().focus === 'agent') {
      window.requestAnimationFrame(function () {
        var body = reader.querySelector('.project-reader-body');
        var section = reader.querySelector('#agent-view');
        if (body && section) body.scrollTop = Math.max(0, section.offsetTop - body.offsetTop - 12);
      });
    }
  }

  function bindReader() {
    host.querySelectorAll('[data-expand-crate]').forEach(function (button) {
      addListener(button, 'click', function () {
        api.setQuery({ scope: 'crate', crate: button.getAttribute('data-expand-crate'),
          module: '', file: '', sel: '', q: '' });
      });
    });
    host.querySelectorAll('[data-expand-module]').forEach(function (button) {
      addListener(button, 'click', function () {
        api.setQuery({ scope: 'module', crate: button.getAttribute('data-crate'),
          module: button.getAttribute('data-expand-module'), file: '', sel: '', q: '' });
      });
    });
    host.querySelectorAll('[data-expand-file]').forEach(function (button) {
      var path = button.getAttribute('data-expand-file');
      addListener(button, 'click', function () {
        var file = fileByPath[path];
        api.setQuery({ scope: 'file', file: path, crate: file ? file.crate : '',
          module: file ? (file.module || '(root)') : '', sel: '', q: '' });
      });
    });
    host.querySelectorAll('[data-open-symbol]').forEach(function (button) {
      addListener(button, 'click', function () {
        var path = button.getAttribute('data-file');
        var file = fileByPath[path];
        api.setQuery({ scope: 'file', file: path, crate: file ? file.crate : '',
          module: file ? (file.module || '(root)') : '', sel: button.getAttribute('data-open-symbol') });
      });
    });
  }

  function render(nextHost, nextState, nextApi) {
    teardown();
    host = nextHost; routeState = nextState; api = nextApi;
    var stateClass = P.status.state === 'current' ? 'ok' : 'warn';
    host.innerHTML =
      '<div class="project-screen">' +
        '<section class="project-status">' +
          '<div class="status-lead"><span class="status-dot ' + stateClass + '"></span><div>' +
            '<span class="eyebrow">Project Intelligence · semantic truth</span>' +
            '<strong>' + esc(P.status.label) + '</strong><p>' + esc(P.status.detail) + '</p></div></div>' +
          '<dl><div><dt>Files</dt><dd>' + P.counts.files.toLocaleString() + '</dd></div>' +
            '<div><dt>Symbols</dt><dd>' + P.counts.nodes.toLocaleString() + '</dd></div>' +
            '<div><dt>Resolved edges</dt><dd>' + P.counts.edges.toLocaleString() + '</dd></div>' +
            '<div><dt>Pending</dt><dd>' + P.counts.pending.toLocaleString() + '</dd></div></dl>' +
          '<details class="trust-contract"><summary>Trust &amp; freshness</summary>' +
            '<div class="trust-grid"><div><span>Source</span><code>' + esc(P.meta.sourceShortCommit) +
            '</code></div><div><span>Schema</span><strong>v' + esc(P.meta.schemaVersion) +
            '</strong></div><div><span>Identity</span><strong>v' + esc(P.meta.nodeIdentityVersion) +
            '</strong></div><div><span>Refreshed</span><time>' + esc(P.meta.generatedAt) +
            '</time></div></div><p><strong>Indexed:</strong> ' + esc(P.meta.includeRule) +
            '. <strong>Excluded:</strong> ' + esc(P.meta.excludeRule) + '.</p>' +
            '<ul><li><strong>Current</strong> — exact commit and fast resync; active now.</li>' +
            '<li><strong>Stale</strong> — shown when source commit moves beyond the index.</li>' +
            '<li><strong>Partial</strong> — shown when tracked source changes or an incomplete root is detected.</li>' +
            '<li><strong>Error</strong> — preserves filesystem truth but disables semantic relation claims.</li></ul>' +
          '</details>' +
        '</section>' +
        '<section class="project-workspace">' +
          '<div class="graph-canvas-wrap" id="project-canvas-wrap">' +
            '<canvas aria-label="Progressive project hierarchy and semantic relation graph" role="img"></canvas>' +
            '<div class="graph-controls project-controls">' +
              '<div class="panel"><button class="icon-btn" type="button" data-project-fit>Fit</button>' +
                '<button class="icon-btn" type="button" data-project-zoom="in" aria-label="Zoom in">+</button>' +
                '<button class="icon-btn" type="button" data-project-zoom="out" aria-label="Zoom out">−</button></div>' +
              '<div class="panel"><label for="project-mode">Edges</label><select id="project-mode">' +
                ['hierarchy', 'dependency', 'call', 'blast'].map(function (item) {
                  var labels = { hierarchy: 'Hierarchy', dependency: 'Resolved relations (calls)',
                    call: 'Calls only', blast: 'Selection / blast radius' };
                  return '<option value="' + item + '"' + (mode() === item ? ' selected' : '') + '>' +
                    labels[item] + '</option>';
                }).join('') + '</select></div>' +
              '<div class="panel project-search"><label class="sr-only" for="project-q">Filter current graph</label>' +
                '<input id="project-q" type="search" value="' + esc(query().q || '') +
                '" placeholder="Filter this level…"></div>' +
              hierarchyNav() +
            '</div>' +
            '<div class="graph-legend project-legend"><h3>Progressive repository graph</h3>' +
              '<p><strong>' + esc(currentModel ? currentModel.title : 'Product workspace') +
              '</strong><br><span id="project-visible-count"></span></p>' +
              '<div class="legend-edges"><span><i></i> persisted semantic edge</span>' +
                '<span><i class="str"></i> hierarchy / definition</span></div>' +
              '<p class="rel-kind">Click to inspect · drag to reposition · drag background to pan · wheel to zoom.</p>' +
            '</div>' +
          '</div>' +
          '<aside class="graph-reader project-reader" id="project-reader" aria-label="Selected project context"></aside>' +
        '</section>' +
      '</div>';

    currentModel = buildModel();
    canvas = host.querySelector('canvas');
    ctx = canvas.getContext('2d');
    readColours(); resize(); fit(); draw(); bindCanvas();
    renderReader();
    var visible = host.querySelector('#project-visible-count');
    if (visible) {
      visible.textContent = count(currentModel.nodes.length, 'visible node') + ' · ' +
        count(currentModel.edges.length, 'visible edge') +
        (currentModel.omitted ? ' · ' + currentModel.omitted + ' omitted for density' : '');
    }

    addListener(window, 'resize', function () { resize(); draw(); });
    addListener(host.querySelector('[data-project-fit]'), 'click', function () { fit(); draw(); });
    host.querySelectorAll('[data-project-zoom]').forEach(function (button) {
      addListener(button, 'click', function () {
        var factor = button.getAttribute('data-project-zoom') === 'in' ? 1.25 : .8;
        var cx = (canvas.__w || 600) / 2, cy = (canvas.__h || 400) / 2;
        var next = Math.max(.15, Math.min(4, view.k * factor));
        view.x = cx - (cx - view.x) * next / view.k;
        view.y = cy - (cy - view.y) * next / view.k;
        view.k = next; draw();
      });
    });
    addListener(host.querySelector('#project-mode'), 'change', function (event) {
      api.setQuery({ mode: event.target.value === 'hierarchy' ? '' : event.target.value, sel: '' });
    });
    var searchTimer;
    addListener(host.querySelector('#project-q'), 'input', function (event) {
      var value = event.target.value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function () { api.setQuery({ q: value }, true); }, 160);
    });
    host.querySelectorAll('[data-scope]').forEach(function (button) {
      addListener(button, 'click', function () {
        var nextScope = button.getAttribute('data-scope');
        api.setQuery({
          scope: nextScope === 'workspace' ? '' : nextScope,
          crate: button.getAttribute('data-crate') || '',
          module: button.getAttribute('data-module') || '',
          file: '', sel: '', q: ''
        });
      });
    });
  }

  function recolour() {
    if (!canvas) return;
    readColours(); draw();
  }

  function teardown() {
    listeners.splice(0).forEach(function (dispose) { dispose(); });
    host = null; canvas = null; ctx = null; currentModel = null; selected = null;
    hover = null; drag = null; pan = null; api = null; routeState = null;
  }

  window.CPProjectUI = { render: render, teardown: teardown, recolour: recolour };
})();
