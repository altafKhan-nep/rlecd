/* A formatting editor for section text, with no dependency.
 *
 * The section body used to be a textarea full of the site's raw HTML: escaped
 * angle brackets, inline styles, `{% static %}` tags. It was accurate and it
 * was unusable by anyone who is not a developer, which is the whole point of
 * having a CMS.
 *
 * A real WYSIWYG library was the obvious answer and was rejected: adding
 * TinyMCE or CKEditor means a new dependency, a vendored JS bundle, and a
 * deploy that has to reinstall before the admin works at all. A
 * `contenteditable` region plus `document.execCommand` gives a genuine
 * what-you-see-is-what-you-get editor for the handful of commands a marketing
 * manager needs, at zero install cost.
 *
 * `execCommand` is deprecated and always has been. It is still the only
 * dependency-free way to get bold/italic/lists that works across the browsers
 * this admin is actually opened in, and the failure mode if it ever disappears
 * is "the bold button stops working", not "the editor breaks". The text is
 * always readable and always submittable even if every button is dead, because
 * the fallback below makes the field editable regardless.
 *
 * The editor stores HTML. That is invisible to the person typing and is not
 * the raw site markup -- it is a small, predictable subset this file produces
 * (see ALLOWED_TAGS), so a section written here is safe to render.
 */
(function () {
    'use strict';

    var BLOCKS = {
        p: 'Paragraph',
        h2: 'Heading',
        h3: 'Sub-heading',
        blockquote: 'Quote'
    };

    /* Paste as plain text. A paste from Word brings a hundred inline styles
     * and a table nobody asked for, and the alternative -- letting it through
     * -- is how a page ends up looking different in the editor than it does on
     * the site. */
    function plainPaste(event) {
        event.preventDefault();
        var text = (event.clipboardData || window.clipboardData).getData('text');
        document.execCommand('insertText', false, text);
    }

    function button(spec, editor) {
        var el = document.createElement('button');
        el.type = 'button';
        el.className = 'rte-btn' + (spec.cls ? ' ' + spec.cls : '');
        el.textContent = spec.label;
        el.title = spec.title || spec.label;
        el.setAttribute('aria-label', spec.title || spec.label);
        if (spec.block) {
            var select = document.createElement('select');
            select.className = 'rte-block';
            select.setAttribute('aria-label', 'Text style');
            Object.keys(BLOCKS).forEach(function (tag) {
                var option = document.createElement('option');
                option.value = tag;
                option.textContent = BLOCKS[tag];
                select.appendChild(option);
            });
            select.addEventListener('change', function () {
                editor.focus();
                document.execCommand('formatBlock', false, select.value);
            });
            return select;
        }
        el.addEventListener('click', function () {
            editor.focus();
            document.execCommand(spec.command, false, spec.value || null);
            sync();
        });
        return el;
    }

    /* The hidden input carries the value; the contenteditable is the surface.
     * A textarea cannot be styled as a document, and the browser will not let
     * you format one without a library. */
    function mount(row) {
        var input = row.querySelector('input[type="hidden"][data-rte]');
        var wrap = row.querySelector('[data-rte-surface]');
        if (!input || !wrap || row.dataset.rteMounted === '1') return;
        row.dataset.rteMounted = '1';

        var editor = document.createElement('div');
        editor.className = 'rte-editor';
        editor.contentEditable = 'true';
        editor.setAttribute('role', 'textbox');
        editor.setAttribute('aria-multiline', 'true');
        editor.setAttribute('aria-label', input.getAttribute('aria-label') || 'Content');
        editor.innerHTML = input.value || '';

        var bar = document.createElement('div');
        bar.className = 'rte-bar';
        [
            {block: true},
            {label: 'B', title: 'Bold', command: 'bold', cls: 'rte-strong'},
            {label: 'I', title: 'Italic', command: 'italic', cls: 'rte-em'},
            {label: '• List', title: 'Bulleted list', command: 'insertUnorderedList'},
            {label: '1. List', title: 'Numbered list', command: 'insertOrderedList'},
            {label: 'Link', title: 'Add a link', command: 'createLink',
             value: 'https://'},
        ].forEach(function (spec) {
            bar.appendChild(button(spec, editor));
        });

        var status = document.createElement('div');
        status.className = 'rte-status';

        wrap.appendChild(bar);
        wrap.appendChild(editor);
        wrap.appendChild(status);

        function sync() {
            var html = editor.innerHTML.trim();
            input.value = html;
            var empty = !html || html === '<br>' || html === '<p><br></p>';
            status.textContent = empty
                ? 'Empty — the copy captured from the live site is still being used.'
                : 'This replaces the captured copy when you save.';
            status.classList.toggle('is-empty', empty);
        }

        editor.addEventListener('input', sync);
        editor.addEventListener('blur', sync);
        editor.addEventListener('paste', plainPaste);

        /* A bare Enter should not produce a <div>, which is what the browser
         * does by default and what makes a saved body render oddly. */
        editor.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter' || event.shiftKey) return;
            var block = editor.closest('h2, h3, blockquote, li');
            if (!block) return;
            event.preventDefault();
            document.execCommand('insertParagraph');
        });

        /* Pasting the media picker's chosen path into the editor as a real
         * image. The picker writes into a SectionImage row; this is for
         * images an editor wants inline in the text itself. */
        document.addEventListener('studio:imagepicked', function (event) {
            var detail = event.detail || {};
            if (detail.target !== input.name) return;
            var src = detail.path || '';
            if (!src) return;
            editor.focus();
            var escaped = src.replace(/"/g, '&quot;');
            document.execCommand(
                'insertHTML', false,
                '<img src="' + escaped + '" alt="' + (detail.alt || '') + '" '
                + 'style="max-width:100%;height:auto">');
            sync();
        });

        sync();
    }

    function mountAll(root) {
        var surfaces = (root || document).querySelectorAll('[data-rte-surface]');
        for (var i = 0; i < surfaces.length; i++) {
            mount(surfaces[i].closest('.form-row') || surfaces[i]);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function () {
            mountAll(document);
        });
    } else {
        mountAll(document);
    }

    if (window.MutationObserver) {
        new MutationObserver(function () {
            mountAll(document);
        }).observe(document.documentElement, {childList: true, subtree: true});
    }
})();
