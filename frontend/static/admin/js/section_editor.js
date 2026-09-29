/* Live preview and image insertion for the section body editor.
 *
 * Section content is raw HTML on purpose: the captured pages are raw HTML, and
 * a WYSIWYG editor would rewrite the markup the mirror regression test compares
 * against. What is missing is feedback, so this adds it without taking the
 * editing model away.
 *
 * The preview is an <iframe> with srcdoc, not an injected <div>. A section
 * carries its own <style> blocks, and injecting into the admin document would
 * let those restyle the whole admin. An iframe gives the section its own
 * document to be wrong in.
 *
 * The preview is also a rough approximation, not the real page. It shows the
 * section's own CSS and the site stylesheet, but not the navbar, footer or
 * the other sections, so "looks right here" is not "looks right on the site".
 * The Publish button on the form is the real check.
 */
(function () {
    'use strict';

    var DEBOUNCE_MS = 500;

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    /* Pull the base stylesheet <link> out of the admin page and reuse it, so
     * the preview inherits the site's own typography and spacing instead of
     * browser defaults. Falls back to nothing, which still previews. */
    function siteStylesheets() {
        var links = document.querySelectorAll('link[rel="stylesheet"]');
        var out = [];
        for (var i = 0; i < links.length; i++) {
            var href = links[i].getAttribute('href') || '';
            if (href.indexOf('/static/css/') !== -1) out.push(href);
        }
        return out;
    }

    var STYLESHEETS = siteStylesheets();

    function buildFrame(doc, bodyHtml) {
        var head = '<meta charset="utf-8">';
        for (var i = 0; i < STYLESHEETS.length; i++) {
            head += '<link rel="stylesheet" href="' + STYLESHEETS[i] + '">';
        }
        /* Neutralise the admin's own inherited styles inside the frame, so the
         * section is judged against the site CSS and not against the chrome it
         * happens to be sitting next to. */
        head += '<style>body{margin:0;padding:16px;background:#fff;}</style>';
        doc.open();
        doc.write('<!DOCTYPE html><html><head>' + head + '</head><body>'
            + bodyHtml + '</body></html>');
        doc.close();
    }

    function editorFor(row) {
        return row.querySelector('textarea[name$="-content_html"], '
            + 'textarea[name="content_html"]');
    }

    function mount(row) {
        var editor = editorFor(row);
        if (!editor || row.dataset.previewMounted === '1') return;
        row.dataset.previewMounted = '1';

        var wrap = el('div', 'section-live-preview');
        var head = el('div', 'section-live-preview-head');
        head.appendChild(el('span', 'section-live-preview-title', 'Live preview'));

        var insert = el('button', 'button section-insert-image', 'Insert image');
        insert.type = 'button';
        insert.title = 'Drop a section-image tag at the cursor';
        head.appendChild(insert);
        wrap.appendChild(head);

        var body = el('div', 'section-live-preview-body');
        var frame = document.createElement('iframe');
        frame.className = 'section-live-preview-frame';
        frame.setAttribute('title', 'Section preview');
        frame.setAttribute('sandbox', 'allow-same-origin');
        body.appendChild(frame);
        wrap.appendChild(body);

        var note = el('div', 'section-preview-empty');
        note.hidden = true;
        wrap.appendChild(note);

        editor.parentNode.insertBefore(wrap, editor.nextSibling);

        var timer = null;
        function refresh() {
            var source = editor.value || '';
            if (!source.trim()) {
                frame.hidden = true;
                note.hidden = false;
                note.textContent = 'This section has no content yet.';
                return;
            }
            note.hidden = true;
            frame.hidden = false;
            try {
                buildFrame(frame.contentDocument, source);
            } catch (err) {
                /* A sandboxed frame can be briefly unavailable; the next
                 * keystroke will try again. */
                frame.hidden = true;
            }
        }

        editor.addEventListener('input', function () {
            clearTimeout(timer);
            timer = setTimeout(refresh, DEBOUNCE_MS);
        });
        editor.addEventListener('change', refresh);

        /* The picker writes into the image field and dispatches input/change,
         * which does not reach this textarea. The marker placeholder is already
         * in the text, so a manual refresh is enough to show it. */
        insert.addEventListener('click', function () {
            var marker = '<!--rlecd-image:NEW-->';
            var start = editor.selectionStart;
            var end = editor.selectionEnd;
            editor.value = editor.value.slice(0, start) + marker
                + editor.value.slice(end);
            editor.focus();
            editor.selectionStart = editor.selectionEnd = start + marker.length;
            editor.dispatchEvent(new Event('input', {bubbles: true}));
            editor.dispatchEvent(new Event('change', {bubbles: true}));
            note.hidden = false;
            note.textContent =
                'Marker inserted. Add the image on the Image row below, then '
                + 'replace NEW with its number.';
        });

        refresh();
    }

    function mountAll(root) {
        var rows = (root || document).querySelectorAll(
            '.form-row.field-content_html, .field-content_html');
        for (var i = 0; i < rows.length; i++) {
            mount(rows[i].closest('.form-row') || rows[i]);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            mountAll(document);
        });
    } else {
        mountAll(document);
    }

    /* Django's inlines.js clones the empty-form template when an editor presses
     * "Add another", and nothing fires for that. Observing the DOM is the only
     * way to mount a preview on a row that did not exist at load. */
    if (window.MutationObserver) {
        new MutationObserver(function () {
            mountAll(document);
        }).observe(document.documentElement, {childList: true, subtree: true});
    }
})();
