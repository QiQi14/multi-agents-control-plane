/* Human task presentation, typed evidence/review, and the sole raw Source view. */
(function () {
  'use strict';

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function array(value) {
    return Array.isArray(value) ? value : [];
  }

  function presentation(task) {
    var value = task && task.presentation;
    if (value && typeof value === 'object') return value;
    return {
      state: 'legacy-unavailable',
      schemaVersion: null,
      purpose: null,
      outcome: null,
      scope: [],
      outOfScope: [],
      acceptance: [],
      unavailable: {
        label: 'Human presentation unavailable',
        guidance: 'This legacy task has no authored presentation contract. Open Source to inspect its complete execution record.',
        sourceActionLabel: 'Open Source'
      },
      technicalFootprint: {
        touched: [],
        offLimits: [],
        unmappedTargetCount: 0,
        unmappedForbiddenCount: 0,
        provisional: false
      },
      delivery: {
        stage: 'planned',
        label: 'Planned ? awaiting execution',
        receiptCount: 0,
        executorReceiptCount: 0,
        qaReceiptCount: 0,
        closed: false,
        accepted: false
      },
      receipts: [],
      evidence: {
        items: [],
        counts: { total: 0, available: 0, unavailable: 0 }
      },
      media: []
    };
  }

  function sourceHref(task) {
    return '#/task/' + encodeURIComponent((task && task.id) || '') + '?view=source';
  }

  function sourceAction(task, label) {
    var p = presentation(task);
    var unavailable = p.unavailable || {};
    return '<a class="btn" href="' + esc(sourceHref(task)) + '">' +
      esc(label || unavailable.sourceActionLabel || 'Open Source') + '</a>';
  }

  function unavailableNotice(task, id) {
    var p = presentation(task);
    var unavailable = p.unavailable || {};
    return '<section class="spec-block evidence-section" id="' + esc(id || 'presentation-unavailable') +
      '" data-state="legacy-unavailable">' +
      '<h2>' + esc(unavailable.label || 'Human presentation unavailable') +
        ' <span class="note">legacy contract</span></h2>' +
      '<p class="truth-empty">' +
        esc(unavailable.guidance ||
          'This task has no authored human presentation. Its complete record remains available in Source.') +
      '</p><p>' + sourceAction(task) + '</p></section>';
  }

  function sequenceLabel(sequence) {
    if (!sequence || typeof sequence !== 'object') return '';
    var kind = String(sequence.kind || '');
    var number = sequence.number;
    if (!kind || !number) return '';
    return kind.charAt(0).toUpperCase() + kind.slice(1) + ' ' + number;
  }

  function receiptTitle(receipt, index) {
    var role = String(receipt.role || 'receipt');
    var sequence = sequenceLabel(receipt.sequence);
    return role.charAt(0).toUpperCase() + role.slice(1) +
      (sequence ? ' · ' + sequence : ' · record ' + (index + 1));
  }

  function sourceOnlyNotice(task, count, noun) {
    count = Number(count) || 0;
    if (!count) return '';
    return '<p class="rel-kind"><strong>Additional detail is available in Source.</strong> ' +
      esc(count + ' ' + noun + (count === 1 ? '' : 's')) + ' remain source-only. ' +
      sourceAction(task, 'Open Source') + '</p>';
  }

  function contextHtml(task, items) {
    if (!items.length) {
      return '<p class="truth-empty">No safe typed findings, limitations, or observations were recorded.</p>';
    }
    return '<ol class="truth-list">' + items.map(function (item) {
      var sourceOnlyCount = array(item.sourceOnlyFields).length;
      var facts = [
        item.type ? '<div><dt>Type</dt><dd>' + esc(item.type) + '</dd></div>' : '',
        item.severity ? '<div><dt>Severity</dt><dd>' + esc(item.severity) + '</dd></div>' : '',
        item.state ? '<div><dt>State</dt><dd>' + esc(item.state) + '</dd></div>' : '',
        typeof item.blocking === 'boolean'
          ? '<div><dt>Blocking</dt><dd>' + (item.blocking ? 'Yes' : 'No') + '</dd></div>' : '',
        item.summary ? '<div><dt>Finding</dt><dd>' + esc(item.summary) + '</dd></div>' : '',
        item.resolution ? '<div><dt>Resolution</dt><dd>' + esc(item.resolution) + '</dd></div>' : '',
        item.owner ? '<div><dt>Owner</dt><dd>' + esc(item.owner) + '</dd></div>' : '',
        item.disposition ? '<div><dt>Disposition</dt><dd>' + esc(item.disposition) + '</dd></div>' : ''
      ].join('');
      return '<li><dl class="truth-kv">' + facts + '</dl>' +
        sourceOnlyNotice(task, sourceOnlyCount, 'context field') + '</li>';
    }).join('') + '</ol>';
  }

  function receiptCards(task, receipts, includeContext) {
    if (!receipts.length) {
      return '<p class="truth-empty">No typed executor or QA receipt summary is available.</p>';
    }
    return '<div class="relgroups">' + receipts.map(function (receipt, index) {
      var status = receipt.status || 'not recorded';
      var notes = array(receipt.notes);
      var sourceOnlyCount =
        array(receipt.sourceOnlyFields).length + (Number(receipt.sourceOnlyNoteCount) || 0);
      return '<article class="card relgroup" data-receipt-role="' + esc(receipt.role || '') + '">' +
        '<h3>' + esc(receiptTitle(receipt, index)) + '</h3>' +
        '<dl class="truth-kv">' +
          (receipt.actor ? '<div><dt>Actor</dt><dd>' + esc(receipt.actor) + '</dd></div>' : '') +
          '<div><dt>Status</dt><dd>' + esc(status) + '</dd></div>' +
          (receipt.result ? '<div><dt>Result</dt><dd>' + esc(receipt.result) + '</dd></div>' : '') +
          '<div><dt>Record</dt><dd>' + (receipt.legacy ? 'Legacy typed boundary' : 'Governed typed receipt') +
          '</dd></div>' +
        '</dl>' +
        (notes.length
          ? '<div style="margin-top:12px"><strong>Notes</strong><ul class="truth-list">' +
              notes.map(function (note) { return '<li>' + esc(note) + '</li>'; }).join('') +
            '</ul></div>'
          : '') +
        sourceOnlyNotice(task, sourceOnlyCount, 'receipt detail') +
        (includeContext
          ? '<div style="margin-top:12px"><strong>Findings and follow-ups</strong>' +
              contextHtml(task, array(receipt.context)) + '</div>'
          : '') +
      '</article>';
    }).join('') + '</div>';
  }

  function mediaHtml(media) {
    if (!media.length) {
      return '<p class="truth-empty">No verified media is available in the human presentation.</p>';
    }
    return '<div class="evidence-media-grid">' + media.map(function (item) {
      var src = String(item.src || '');
      var mediaType = String(item.type || '').toLowerCase();
      var reference = item.kind === 'expected-reference';
      var alt = String(item.alt || 'Evidence media.');
      var tag = mediaType.indexOf('video/') === 0
        ? '<video src="' + esc(src) + '" controls preload="metadata" aria-label="' + esc(alt) + '"></video>'
        : (mediaType.indexOf('audio/') === 0
          ? '<audio src="' + esc(src) + '" controls preload="metadata" aria-label="' + esc(alt) + '"></audio>'
          : '<img src="' + esc(src) + '" alt="' + esc(alt) + '" loading="lazy">');
      var dimensions = item.dimensions && item.dimensions.width && item.dimensions.height
        ? item.dimensions.width + ' × ' + item.dimensions.height
        : '';
      return '<figure class="evidence-media">' + tag +
        '<figcaption><strong>' + esc(alt) + '</strong>' +
          (reference
            ? '<span class="evidence-role reference">Expected reference - not generated evidence</span>'
            : '<span class="evidence-role">Recorded evidence</span>') +
          (dimensions ? '<span class="availability">' + esc(dimensions) + '</span>' : '') +
        '</figcaption></figure>';
    }).join('') + '</div>';
  }

  function evidenceItemsHtml(task, evidence) {
    var items = array(evidence.items);
    var counts = evidence.counts || {};
    if (!items.length) {
      return '<p class="truth-empty">No typed evidence claims are available in the human presentation.</p>';
    }
    return '<p class="result-count">' + esc(counts.total == null ? items.length : counts.total) +
      ' total · ' + esc(counts.available || 0) + ' available · ' +
      esc(counts.unavailable || 0) + ' unavailable</p>' +
      '<ol class="truth-list">' + items.map(function (item) {
        return '<li><dl class="truth-kv">' +
          (item.claim ? '<div><dt>Claim</dt><dd>' + esc(item.claim) + '</dd></div>' : '') +
          '<div><dt>Kind</dt><dd>' + esc(item.kind || 'not recorded') + '</dd></div>' +
          '<div><dt>Role</dt><dd>' + esc(item.role || 'not recorded') + '</dd></div>' +
          '<div><dt>Availability</dt><dd>' + esc(item.availability || 'not recorded') + '</dd></div>' +
        '</dl>' + sourceOnlyNotice(task, array(item.sourceOnlyFields).length, 'evidence field') + '</li>';
      }).join('') + '</ol>';
  }

  function evidence(task) {
    var p = presentation(task);
    var record = p.evidence && typeof p.evidence === 'object' ? p.evidence : { items: [], counts: {} };
    return '<div class="task-truth-view" data-task-view="evidence" data-state="' + esc(p.state) + '">' +
      (p.state === 'legacy-unavailable' ? unavailableNotice(task, 'evidence-presentation-state') : '') +
      '<section class="spec-block evidence-section" id="evidence-media">' +
        '<h2>Representative media <span class="note">verified reader assets</span></h2>' +
        mediaHtml(array(p.media)) +
      '</section>' +
      '<section class="spec-block evidence-section" id="evidence-claims">' +
        '<h2>Evidence <span class="note">typed claims and availability</span></h2>' +
        evidenceItemsHtml(task, record) +
      '</section>' +
      '<section class="spec-block evidence-section" id="evidence-receipts">' +
        '<h2>Receipts <span class="note">Executor and QA notes · one summary per governed event</span></h2>' +
        receiptCards(task, array(p.receipts), false) +
      '</section>' +
    '</div>';
  }

  function review(task) {
    var p = presentation(task);
    return '<div class="task-truth-view" data-task-view="review" data-state="' + esc(p.state) + '">' +
      (p.state === 'legacy-unavailable' ? unavailableNotice(task, 'review-presentation-state') : '') +
      '<section class="spec-block evidence-section" id="review-receipts">' +
        '<h2>Review and follow-ups <span class="note">typed findings, resolutions, and dispositions</span></h2>' +
        receiptCards(task, array(p.receipts), true) +
      '</section>' +
    '</div>';
  }

  function sourceRecord(task) {
    if (task && task.sourceProjection && typeof task.sourceProjection === 'object') {
      return task.sourceProjection;
    }
    return { state: 'unavailable', taskId: task && task.id };
  }

  function source(task) {
    var record = sourceRecord(task);
    var sourceText = typeof record === 'string' ? record : JSON.stringify(record, null, 2);
    return '<div class="task-truth-view source-artifacts" data-task-view="source">' +
      '<p class="source-note"><strong>Complete source projection.</strong> This is the sole lossless task ' +
        'view. The full contract, locations, commands, revisions, receipts, evidence, context, and closeout ' +
        'are shown together once.</p>' +
      '<section class="spec-block source-artifact" id="source-record">' +
        '<h2>Canonical task and audit record</h2>' +
        (sourceText
          ? '<pre class="source-view"><code>' + esc(sourceText) + '</code></pre>'
          : '<p class="truth-empty">Source content was not exported for this task.</p>') +
      '</section>' +
    '</div>';
  }

  function toc(view, task) {
    var p = presentation(task);
    if (view === 'source') return [];
    if (view === 'evidence') {
      return (p.state === 'legacy-unavailable'
        ? [['evidence-presentation-state', 'Presentation state']]
        : []).concat([
        ['evidence-media', 'Representative media'],
        ['evidence-claims', 'Evidence'],
        ['evidence-receipts', 'Receipts']
      ]);
    }
    if (view === 'review') {
      return (p.state === 'legacy-unavailable'
        ? [['review-presentation-state', 'Presentation state']]
        : []).concat([['review-receipts', 'Review and follow-ups']]);
    }
    return [
      ['purpose', 'Purpose'],
      ['outcome', 'Required outcome'],
      ['scope', 'Scope'],
      ['acceptance', 'Acceptance'],
      ['relationships', 'Relationships']
    ];
  }

  window.CPTaskRich = {
    evidence: evidence,
    presentation: presentation,
    review: review,
    source: source,
    sourceAction: sourceAction,
    toc: toc,
    unavailableNotice: unavailableNotice
  };
})();
