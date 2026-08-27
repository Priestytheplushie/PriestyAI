import os
import re
import sys
import json
import base64
import shutil
import asyncio
import logging
from collections import defaultdict
from typing import Any
from aiohttp import web
from core.branch_manager import branch_manager

logger = logging.getLogger("PriestyAI.PlaygroundServer")

PLAYGROUND_PORT = 8085

PLAYGROUND_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0, minimum-scale=0.5, maximum-scale=5.0, user-scalable=yes" />
  <title id="page-title">PriestyAI Artifact — {{FILENAME}} (v{{VERSION}})</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg-base: #181818;
      --bg-surface: #1f1f1f;
      --bg-subtle: #252526;
      --border: #2d2d2d;
      --border-focus: #3e3e42;
      --text-main: #cccccc;
      --text-muted: #858585;
      --accent: #007acc;
      --accent-hover: #0062a3;
      --tab-active-bg: #1e1e1e;
      --tab-inactive-bg: #2d2d2d;
      --font-ui: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
      --font-code: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
    }
    body {
      background-color: var(--bg-base);
      color: var(--text-main);
      font-family: var(--font-ui);
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      -webkit-font-smoothing: antialiased;
    }
    header {
      height: 44px;
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 12px;
      user-select: none;
      z-index: 10;
    }
    .header-left { display: flex; align-items: center; gap: 8px; min-width: 0; }
    .file-name {
      font-size: 13px;
      font-weight: 600;
      color: var(--text-main);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .version-badge {
      font-size: 10px;
      font-weight: 600;
      background: var(--bg-subtle);
      color: var(--text-muted);
      padding: 2px 6px;
      border-radius: 10px;
      border: 1px solid var(--border);
      font-family: var(--font-code);
      flex-shrink: 0;
      transition: all 0.3s ease;
    }
    .header-center {
      display: flex;
      align-items: center;
      background: var(--bg-base);
      border: 1px solid var(--border);
      padding: 2px;
      border-radius: 6px;
    }
    .view-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 12px;
      font-weight: 500;
      font-family: var(--font-ui);
      padding: 3px 10px;
      border-radius: 4px;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .view-btn:hover { color: var(--text-main); }
    .view-btn.active {
      background: var(--bg-subtle);
      color: var(--text-main);
      font-weight: 600;
    }
    .header-right { display: flex; align-items: center; gap: 6px; flex-shrink: 0; }
    .action-btn {
      background: var(--bg-subtle);
      border: 1px solid var(--border);
      color: var(--text-main);
      font-size: 12px;
      font-weight: 500;
      font-family: var(--font-ui);
      padding: 4px 10px;
      border-radius: 5px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 5px;
      transition: background 0.15s ease;
    }
    .action-btn:hover { background: var(--border-focus); }
    .action-btn.primary {
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }
    .action-btn.primary:hover { background: var(--accent-hover); }

    main {
      flex: 1;
      display: flex;
      position: relative;
      overflow: hidden;
    }
    .pane {
      flex: 1;
      height: 100%;
      position: relative;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    
    /* VS Code File Tabs Bar */
    .tabs-bar {
      height: 34px;
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: stretch;
      overflow-x: auto;
      overflow-y: hidden;
      user-select: none;
      scrollbar-width: none;
    }
    .tabs-bar::-webkit-scrollbar { display: none; }
    .file-tab {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 0 14px;
      font-size: 12px;
      color: var(--text-muted);
      background: var(--tab-inactive-bg);
      border-right: 1px solid var(--bg-surface);
      cursor: pointer;
      white-space: nowrap;
      transition: background 0.1s, color 0.1s;
    }
    .file-tab:hover {
      background: #232323;
      color: #e0e0e0;
    }
    .file-tab.active {
      background: var(--tab-active-bg);
      color: #ffffff;
      border-top: 2px solid var(--accent);
    }
    .file-tab-icon {
      font-size: 12px;
      opacity: 0.85;
    }

    #editor-container { flex: 1; width: 100%; height: 100%; }
    .divider { width: 1px; background: var(--border); z-index: 5; }
    
    .preview-header {
      height: 34px;
      background: var(--bg-surface);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 12px;
      font-size: 11px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-muted);
      user-select: none;
    }

    /* Zoom Controls Bar */
    .zoom-controls {
      display: inline-flex;
      align-items: center;
      gap: 2px;
      background: var(--bg-base);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 1px 4px;
      margin-left: 8px;
    }
    .zoom-btn {
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      padding: 1px 6px;
      border-radius: 3px;
      line-height: 1;
      transition: all 0.15s ease;
    }
    .zoom-btn:hover {
      background: var(--bg-subtle);
      color: var(--text-main);
    }
    .zoom-level {
      font-size: 11px;
      font-family: var(--font-code);
      color: var(--text-muted);
      padding: 0 4px;
      cursor: pointer;
      min-width: 36px;
      text-align: center;
      user-select: none;
    }
    .zoom-level:hover {
      color: var(--text-main);
    }

    #preview-frame {
      width: 100%;
      height: 100%;
      border: none;
      background: #0d1117;
    }

    @media (max-width: 768px) {
      header { padding: 0 8px; }
      #btn-split { display: none !important; }
      .file-name { max-width: 120px; }
      .action-btn span { display: none; }
      .tabs-bar { height: 30px; }
      .file-tab { font-size: 11px; padding: 0 10px; }
      .preview-header { font-size: 10px; height: 30px; padding: 0 8px; }
      .zoom-controls { margin-left: 4px; padding: 0 2px; }
      .zoom-btn { padding: 1px 4px; font-size: 12px; }
      .zoom-level { font-size: 10px; min-width: 30px; }
    }
  </style>
</head>
<body>
  <header>
    <div class="header-left">
      <div class="file-name">{{FILENAME}}</div>
      <div class="version-badge" id="v-badge">v{{VERSION}} {{DIFF_BADGE}}</div>
    </div>
    <div class="header-center" id="center-controls" style="display: {{CENTER_CONTROLS_DISPLAY}};">
      <button class="view-btn" id="btn-split" onclick="setViewMode('split')">Split</button>
      <button class="view-btn" id="btn-code" onclick="setViewMode('code')">Code</button>
      <button class="view-btn active" id="btn-preview" onclick="setViewMode('preview')">Preview</button>
    </div>
    <div class="header-right">
      <button class="action-btn" onclick="copyCode()">
        <span id="copy-text">Copy</span>
      </button>
      <button class="action-btn primary" onclick="downloadArtifact()">
        Download
      </button>
    </div>
  </header>

  <main id="workspace">
    <section class="pane" id="left-pane">
      <div class="tabs-bar" id="file-tabs-bar" style="display: {{TABS_BAR_DISPLAY}};"></div>
      <div id="editor-container"></div>
    </section>

    <div class="divider" id="pane-divider" style="display: {{DIVIDER_DISPLAY}};"></div>

    <section class="pane" id="right-pane" style="display: {{RIGHT_PANE_DISPLAY}};">
      <div class="preview-header">
        <div style="display: flex; align-items: center; min-width: 0;">
          <span id="preview-title">Document Preview</span>
          <div class="zoom-controls" id="zoom-controls">
            <button class="zoom-btn" onclick="adjustZoom(-0.1)" title="Zoom Out (Ctrl -)">−</button>
            <span class="zoom-level" id="zoom-level-text" onclick="resetZoom()" title="Reset Zoom (Ctrl 0)">100%</span>
            <button class="zoom-btn" onclick="adjustZoom(0.1)" title="Zoom In (Ctrl +)">+</button>
          </div>
        </div>
      </div>
      <iframe id="preview-frame" sandbox="allow-scripts allow-modals allow-same-origin allow-popups allow-forms"></iframe>
    </section>
  </main>

  <script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/marked/12.0.2/marked.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs/loader.min.js"></script>
  <script>
    function decodeBase64Utf8(base64Str) {
      try {
        const binaryString = atob(base64Str);
        const bytes = new Uint8Array(binaryString.length);
        for (let i = 0; i < binaryString.length; i++) {
          bytes[i] = binaryString.charCodeAt(i);
        }
        return new TextDecoder("utf-8").decode(bytes);
      } catch (e) {
        return "";
      }
    }

    function detectLang(filename) {
      const ext = (filename || '').split('.').pop().toLowerCase();
      const map = {
        'html': 'html', 'htm': 'html', 'svg': 'html',
        'js': 'javascript', 'mjs': 'javascript', 'cjs': 'javascript',
        'ts': 'typescript', 'tsx': 'typescript', 'jsx': 'javascript',
        'py': 'python', 'rs': 'rust', 'go': 'go',
        'cpp': 'cpp', 'c': 'c', 'h': 'c', 'hpp': 'cpp',
        'java': 'java', 'sh': 'shell', 'bash': 'shell',
        'json': 'json', 'css': 'css', 'scss': 'scss',
        'md': 'markdown', 'markdown': 'markdown', 'sql': 'sql', 'yaml': 'yaml', 'yml': 'yaml'
      };
      return map[ext] || 'plaintext';
    }

    function getFileTabIcon(filename) {
      const ext = (filename || '').split('.').pop().toLowerCase();
      const icons = {
        'html': '🌐', 'css': '🎨', 'js': '⚡', 'ts': '🔷', 'jsx': '⚛️', 'tsx': '⚛️',
        'py': '🐍', 'rs': '🦀', 'go': '🐹', 'json': '📋', 'md': '📝', 'markdown': '📝', 'svg': '🖼️'
      };
      return icons[ext] || '📄';
    }

    const ARTIFACT_ID = "{{ARTIFACT_ID}}";
    let currentVersion = parseInt("{{VERSION}}", 10) || 1;
    const RAW_FILES_B64 = "{{RAW_FILES_B64}}";
    let projectFiles = JSON.parse(decodeBase64Utf8(RAW_FILES_B64) || "[]");
    let ARTIFACT_FILENAME = "{{FILENAME}}";
    let IS_MULTI_FILE = projectFiles.length > 1 || ARTIFACT_FILENAME.endsWith('.zip');
    let IS_PREVIEWABLE = {{IS_PREVIEWABLE_BOOL}};

    let currentEditor = null;
    let activeFileIndex = 0;
    let renderDebounceTimer = null;
    let currentDocZoom = 1.0;
    let currentMode = 'code';

    function isMarkdownFile(fname) {
      const lower = (fname || '').toLowerCase();
      return lower.endsWith('.md') || lower.endsWith('.markdown');
    }

    function setDocZoom(zoom) {
      currentDocZoom = Math.min(2.5, Math.max(0.5, Math.round(zoom * 10) / 10));
      const zoomText = document.getElementById('zoom-level-text');
      if (zoomText) {
        zoomText.innerText = Math.round(currentDocZoom * 100) + '%';
      }
      
      const iframe = document.getElementById('preview-frame');
      if (iframe) {
        try {
          if (iframe.contentDocument && iframe.contentDocument.body) {
            iframe.contentDocument.body.style.zoom = currentDocZoom;
          }
        } catch (e) {}
      }
    }

    function adjustZoom(delta) {
      setDocZoom(currentDocZoom + delta);
    }

    function resetZoom() {
      setDocZoom(1.0);
    }

    window.addEventListener('keydown', function(e) {
      if ((e.ctrlKey || e.metaKey) && (e.key === '=' || e.key === '+')) {
        e.preventDefault();
        adjustZoom(0.1);
      } else if ((e.ctrlKey || e.metaKey) && e.key === '-') {
        e.preventDefault();
        adjustZoom(-0.1);
      } else if ((e.ctrlKey || e.metaKey) && e.key === '0') {
        e.preventDefault();
        resetZoom();
      }
    });

    function handlePreviewNavigation(href) {
      if (!href) return;
      if (href.startsWith('javascript:')) return;

      // Handle external protocols
      if (/^(?:[a-z]+:)?\/\//i.test(href) || href.startsWith('mailto:') || href.startsWith('tel:')) {
        window.open(href, '_blank', 'noopener,noreferrer');
        return;
      }

      // Strip query/hash and leading slashes/dots
      const cleanHref = href.replace(/^(\.\/|\/)/, '').split('?')[0].split('#')[0].toLowerCase();
      
      // Look for match in multi-file project files
      const targetIndex = projectFiles.findIndex(function(f) {
        const fn = (f.filename || '').toLowerCase().replace(/^(\.\/|\/)/, '');
        return fn === cleanHref || fn.endsWith('/' + cleanHref) || cleanHref.endsWith('/' + fn);
      });

      if (targetIndex !== -1) {
        switchFileTab(targetIndex);
      } else {
        // Prevent recursive playground nesting inside the iframe
        if (href.includes('/p/')) {
          window.open(href, '_blank');
        } else {
          window.open(href, '_blank', 'noopener,noreferrer');
        }
      }
    }

    window.addEventListener('message', function(event) {
      if (event.data && event.data.type === 'priesty_navigate') {
        handlePreviewNavigation(event.data.href);
      }
    });

    if (window.marked) {
      marked.use({
        gfm: true,
        breaks: true,
        renderer: {
          code(token) {
            const text = token.text || '';
            const lang = token.lang || '';
            const validLang = !!(lang && window.hljs && hljs.getLanguage(lang));
            const highlighted = validLang 
              ? hljs.highlight(text, { language: lang }).value 
              : (window.hljs ? hljs.highlightAuto(text).value : text);
            return '<pre><code class="hljs ' + (validLang ? lang : '') + '">' + highlighted + '<' + '/code><' + '/pre>';
          }
        }
      });
    }

    function preprocessGfmAlerts(md) {
      if (!md) return '';
      const alertRegex = /^>\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*\n((?:^>[^\n]*\n?)*)/gim;
      const iconMap = {
        'NOTE': 'ℹ️',
        'TIP': '💡',
        'IMPORTANT': '📌',
        'WARNING': '⚠️',
        'CAUTION': '🛑'
      };

      return md.replace(alertRegex, function(match, alertType, body) {
        const type = alertType.toUpperCase();
        const icon = iconMap[type] || '💡';
        const cleanBody = body.replace(/^>\s?/gm, '').trim();
        return '\n<div class="markdown-alert markdown-alert-' + type.toLowerCase() + '">\n<div class="markdown-alert-title"><span class="alert-icon">' + icon + '</span> ' + (type.charAt(0) + type.slice(1).toLowerCase()) + '</div>\n\n' + cleanBody + '\n</div>\n\n';
      });
    }

    function renderMarkdownTemplate(parsedMd) {
      return '<!DOCTYPE html>\n' +
'<html>\n' +
'<head>\n' +
'  <meta charset="utf-8">\n' +
'  <meta name="viewport" content="width=device-width, initial-scale=1, minimum-scale=0.5, maximum-scale=5.0, user-scalable=yes">\n' +
'  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/github-markdown-css/5.5.1/github-markdown-dark.min.css">\n' +
'  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/github-dark.min.css">\n' +
'  <style>\n' +
'    html, body {\n' +
'      background-color: #0d1117 !important;\n' +
'      color: #c9d1d9 !important;\n' +
'      margin: 0;\n' +
'      padding: 24px;\n' +
'      box-sizing: border-box;\n' +
'      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;\n' +
'      zoom: ' + currentDocZoom + ';\n' +
'      touch-action: pan-x pan-y pinch-zoom;\n' +
'    }\n' +
'    .markdown-body {\n' +
'      background-color: #0d1117 !important;\n' +
'      color: #c9d1d9 !important;\n' +
'      max-width: 860px;\n' +
'      margin: 0 auto;\n' +
'      line-height: 1.6;\n' +
'      font-size: 14px;\n' +
'    }\n' +
'    h1, h2, h3, h4, h5, h6 {\n' +
'      color: #f0f6fc !important;\n' +
'      border-bottom: 1px solid #21262d !important;\n' +
'      font-weight: 600;\n' +
'    }\n' +
'    pre {\n' +
'      background-color: #161b22 !important;\n' +
'      border: 1px solid #30363d !important;\n' +
'      border-radius: 6px;\n' +
'      padding: 16px;\n' +
'      overflow: auto;\n' +
'    }\n' +
'    code {\n' +
'      font-family: "JetBrains Mono", Consolas, monospace !important;\n' +
'    }\n' +
'    table {\n' +
'      border-collapse: collapse;\n' +
'      width: 100%;\n' +
'      margin: 16px 0;\n' +
'    }\n' +
'    th, td {\n' +
'      border: 1px solid #30363d;\n' +
'      padding: 8px 12px;\n' +
'    }\n' +
'    th {\n' +
'      background-color: #161b22;\n' +
'    }\n' +
'    .markdown-alert {\n' +
'      padding: 10px 16px;\n' +
'      margin-bottom: 16px;\n' +
'      border-left: 3.5px solid;\n' +
'      border-radius: 6px;\n' +
'      background-color: #161b22;\n' +
'      box-shadow: 0 1px 3px rgba(0,0,0,0.2);\n' +
'    }\n' +
'    .markdown-alert-title {\n' +
'      display: flex;\n' +
'      align-items: center;\n' +
'      gap: 6px;\n' +
'      font-weight: 600;\n' +
'      font-size: 13px;\n' +
'      line-height: 1.4;\n' +
'      margin-bottom: 6px !important;\n' +
'    }\n' +
'    .markdown-alert p:last-child {\n' +
'      margin-bottom: 0;\n' +
'    }\n' +
'    .markdown-alert.markdown-alert-note { border-left-color: #1f6feb; }\n' +
'    .markdown-alert.markdown-alert-note .markdown-alert-title { color: #58a6ff; }\n' +
'    .markdown-alert.markdown-alert-tip { border-left-color: #238636; }\n' +
'    .markdown-alert.markdown-alert-tip .markdown-alert-title { color: #3fb950; }\n' +
'    .markdown-alert.markdown-alert-important { border-left-color: #8957e5; }\n' +
'    .markdown-alert.markdown-alert-important .markdown-alert-title { color: #a371f7; }\n' +
'    .markdown-alert.markdown-alert-warning { border-left-color: #9e6a03; }\n' +
'    .markdown-alert.markdown-alert-warning .markdown-alert-title { color: #d29922; }\n' +
'    .markdown-alert.markdown-alert-caution { border-left-color: #da3633; }\n' +
'    .markdown-alert.markdown-alert-caution .markdown-alert-title { color: #f85149; }\n' +
'  </style>\n' +
'  <script>\n' +
'    window.addEventListener("wheel", function(e) { if (e.ctrlKey || e.metaKey) { e.preventDefault(); window.parent.adjustZoom(e.deltaY < 0 ? 0.1 : -0.1); } }, { passive: false });\n' +
'    document.addEventListener("click", function(e) {\n' +
'      var a = e.target.closest("a");\n' +
'      if (!a) return;\n' +
'      var href = a.getAttribute("href");\n' +
'      if (!href || href.startsWith("javascript:")) return;\n' +
'      e.preventDefault();\n' +
'      e.stopPropagation();\n' +
'      if (href.startsWith("#")) {\n' +
'        if (href === "#" || href === "") {\n' +
'          window.scrollTo({ top: 0, behavior: "smooth" });\n' +
'        } else {\n' +
'          try {\n' +
'            var targetId = href.slice(1);\n' +
'            var elem = document.getElementById(targetId) || document.querySelector(\'[name="\' + CSS.escape(targetId) + \'"]\');\n' +
'            if (elem) elem.scrollIntoView({ behavior: "smooth" });\n' +
'          } catch (err) {}\n' +
'        }\n' +
'        return;\n' +
'      }\n' +
'      try {\n' +
'        window.parent.postMessage({ type: "priesty_navigate", href: href }, "*");\n' +
'      } catch (err) {}\n' +
'    }, true);\n' +
'    document.addEventListener("submit", function(e) { e.preventDefault(); e.stopPropagation(); }, true);\n' +
'  <' + '/script>\n' +
'</head>\n' +
'<body class="markdown-body">\n' +
parsedMd + '\n' +
'<' + '/body>\n' +
'<' + '/html>';
    }

    function getBundledHtml() {
      if (!IS_PREVIEWABLE) return '';

      // 1. If currently selected file in tabs is a Markdown file, render it directly
      const currentFile = projectFiles[activeFileIndex];
      if (currentFile && isMarkdownFile(currentFile.filename)) {
        const rawContent = currentFile.content || '';
        const preprocessed = preprocessGfmAlerts(rawContent);
        const parsedMd = window.marked ? marked.parse(preprocessed) : preprocessed;
        return renderMarkdownTemplate(parsedMd);
      }

      // 2. Look for HTML entry point: prefer the currently active tab if it is HTML
      let htmlFile = null;
      if (currentFile && ((currentFile.filename || '').toLowerCase().endsWith('.html') || (currentFile.filename || '').toLowerCase().endsWith('.htm'))) {
        htmlFile = currentFile;
      } else {
        htmlFile = projectFiles.find(function(f) {
          const fn = (f.filename || '').toLowerCase();
          return fn === 'index.html' || fn.endsWith('/index.html');
        }) || projectFiles.find(function(f) { 
          const fn = (f.filename || '').toLowerCase();
          return fn.endsWith('.html') || fn.endsWith('.htm'); 
        });
      }

      if (htmlFile) {
        let htmlCode = htmlFile.content || '';

        projectFiles.forEach(function(f) {
          if ((f.filename || '').toLowerCase().endsWith('.css')) {
            const escaped = f.filename.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const linkRegex = new RegExp('<link[^>]*href=["\'](?:./)?' + escaped + '["\'][^>]*>', 'gi');
            if (linkRegex.test(htmlCode)) {
              htmlCode = htmlCode.replace(linkRegex, '<style>\n/* Inlined ' + f.filename + ' */\n' + f.content + '\n<' + '/style>');
            } else if (htmlCode.includes('</' + 'head>')) {
              htmlCode = htmlCode.replace('</' + 'head>', '<style>\n/* Inlined ' + f.filename + ' */\n' + f.content + '\n<' + '/style></' + 'head>');
            }
          }
        });

        projectFiles.forEach(function(f) {
          const fn = (f.filename || '').toLowerCase();
          if (fn.endsWith('.js') && !fn.endsWith('.min.js')) {
            const escaped = f.filename.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
            const scriptRegex = new RegExp('<script[^>]*src=["\'](?:./)?' + escaped + '["\'][^>]*>\\s*<' + '\\/script>', 'gi');
            if (scriptRegex.test(htmlCode)) {
              htmlCode = htmlCode.replace(scriptRegex, '<script>\n/* Inlined ' + f.filename + ' */\n' + f.content + '\n<' + '/script>');
            } else if (htmlCode.includes('</' + 'body>')) {
              htmlCode = htmlCode.replace('</' + 'body>', '<script>\n/* Inlined ' + f.filename + ' */\n' + f.content + '\n<' + '/script></' + 'body>');
            }
          }
        });

        const navInterceptionScript = '<script>\n' +
          '(function() {\n' +
          '  document.addEventListener("click", function(e) {\n' +
          '    var a = e.target.closest("a");\n' +
          '    if (!a) return;\n' +
          '    var href = a.getAttribute("href");\n' +
          '    if (!href || href.startsWith("javascript:")) return;\n' +
          '    e.preventDefault();\n' +
          '    e.stopPropagation();\n' +
          '    if (href.startsWith("#")) {\n' +
          '      if (href === "#" || href === "") {\n' +
          '        window.scrollTo({ top: 0, behavior: "smooth" });\n' +
          '      } else {\n' +
          '        try {\n' +
          '          var targetId = href.slice(1);\n' +
          '          var elem = document.getElementById(targetId) || document.querySelector(\'[name="\' + CSS.escape(targetId) + \'"]\');\n' +
          '          if (elem) elem.scrollIntoView({ behavior: "smooth" });\n' +
          '        } catch (err) {}\n' +
          '      }\n' +
          '      return;\n' +
          '    }\n' +
          '    try {\n' +
          '      window.parent.postMessage({ type: "priesty_navigate", href: href }, "*");\n' +
          '    } catch (err) {}\n' +
          '  }, true);\n' +
          '  document.addEventListener("submit", function(e) { e.preventDefault(); e.stopPropagation(); }, true);\n' +
          '})();\n' +
          '<' + '/script>';

        if (htmlCode.includes('</' + 'body>')) {
          htmlCode = htmlCode.replace('</' + 'body>', navInterceptionScript + '\n</' + 'body>');
        } else {
          htmlCode += '\n' + navInterceptionScript;
        }

        return htmlCode;
      }

      // 3. Look for Markdown fallback document (e.g. README.md or plan.md in multi-file projects)
      let mdFile = projectFiles.find(function(f) { return isMarkdownFile(f.filename); });
      if (mdFile) {
        const rawContent = mdFile.content || '';
        const preprocessed = preprocessGfmAlerts(rawContent);
        const parsedMd = window.marked ? marked.parse(preprocessed) : preprocessed;
        return renderMarkdownTemplate(parsedMd);
      }

      // 4. Look for SVG vector asset
      let svgFile = projectFiles.find(function(f) { return (f.filename || '').toLowerCase().endsWith('.svg'); });
      if (svgFile) {
        return svgFile.content || '';
      }

      return '';
    }

    function updateLiveIframe() {
      if (!IS_PREVIEWABLE) return;
      const iframe = document.getElementById('preview-frame');
      if (iframe) {
        const bundledHtml = getBundledHtml();
        if (bundledHtml) {
          iframe.srcdoc = bundledHtml;
          iframe.onload = function() {
            try {
              if (iframe.contentDocument && iframe.contentDocument.body) {
                iframe.contentDocument.body.style.zoom = currentDocZoom;
              }
            } catch (e) {}
          };
        }
      }
    }

    function setViewMode(mode) {
      currentMode = mode;
      document.querySelectorAll('.view-btn').forEach(function(b) { b.classList.remove('active'); });
      const activeBtn = document.getElementById('btn-' + mode);
      if (activeBtn) activeBtn.classList.add('active');

      const left = document.getElementById('left-pane');
      const right = document.getElementById('right-pane');
      const divider = document.getElementById('pane-divider');

      if (!IS_PREVIEWABLE || mode === 'code') {
        left.style.display = 'flex';
        right.style.display = 'none';
        divider.style.display = 'none';
      } else if (mode === 'split') {
        left.style.display = 'flex';
        right.style.display = 'flex';
        divider.style.display = 'block';
      } else if (mode === 'preview') {
        left.style.display = 'none';
        right.style.display = 'flex';
        divider.style.display = 'none';
      }

      if (currentEditor) {
        setTimeout(function() { currentEditor.layout(); }, 50);
      }
    }

    function copyCode() {
      if (!currentEditor) return;
      navigator.clipboard.writeText(currentEditor.getValue());
      const label = document.getElementById('copy-text');
      label.innerText = 'Copied!';
      setTimeout(function() { label.innerText = 'Copy'; }, 1500);
    }

    async function downloadArtifact() {
      if (!currentEditor) return;
      if (IS_MULTI_FILE && window.JSZip) {
        const zip = new JSZip();
        projectFiles.forEach(function(f) {
          zip.file(f.filename, f.content);
        });
        const zipBlob = await zip.generateAsync({ type: 'blob' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(zipBlob);
        a.download = ARTIFACT_FILENAME.endsWith('.zip') ? ARTIFACT_FILENAME : (ARTIFACT_FILENAME.split('.')[0] + '.zip');
        a.click();
      } else {
        const activeFile = projectFiles[activeFileIndex] || { filename: ARTIFACT_FILENAME, content: currentEditor.getValue() };
        const blob = new Blob([activeFile.content], { type: 'text/plain' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = activeFile.filename;
        a.click();
      }
    }

    function renderFileTabs() {
      const tabsBar = document.getElementById('file-tabs-bar');
      tabsBar.innerHTML = '';

      if (projectFiles.length > 1) {
        tabsBar.style.display = 'flex';
        projectFiles.forEach(function(file, index) {
          const tab = document.createElement('div');
          tab.className = 'file-tab ' + (index === activeFileIndex ? 'active' : '');
          tab.innerHTML = '<span class="file-tab-icon">' + getFileTabIcon(file.filename) + '</span><span>' + file.filename + '</span>';
          tab.onclick = function() { switchFileTab(index); };
          tabsBar.appendChild(tab);
        });
      } else {
        tabsBar.style.display = 'none';
      }
    }

    function switchFileTab(index) {
      if (index < 0 || index >= projectFiles.length || !currentEditor) return;
      activeFileIndex = index;
      renderFileTabs();

      const targetFile = projectFiles[index];
      currentEditor.setModel(targetFile.model);
      
      const prevTitle = document.getElementById('preview-title');
      if (prevTitle) {
        prevTitle.innerText = isMarkdownFile(targetFile.filename) ? "Document Preview" : "Live Preview";
      }

      if (IS_PREVIEWABLE) {
        updateLiveIframe();
      }
    }

    function initMonacoModels() {
      projectFiles.forEach(function(file) {
        const lang = detectLang(file.filename);
        if (file.model) {
          file.model.dispose();
        }
        if (window.monaco && monaco.editor) {
          file.model = monaco.editor.createModel(file.content, lang);
          file.model.onDidChangeContent(function() {
            file.content = file.model.getValue();
            if (IS_PREVIEWABLE) {
              clearTimeout(renderDebounceTimer);
              renderDebounceTimer = setTimeout(updateLiveIframe, 250);
            }
          });
        }
      });
    }

    function applyNewArtifactVersion(data) {
      if (!data || !data.active_version) return;
      currentVersion = data.active_version;

      document.getElementById('page-title').innerText = 'PriestyAI Artifact — ' + data.filename + ' (v' + currentVersion + ')';
      const badge = document.getElementById('v-badge');
      if (badge) {
        const adds = data.additions || 0;
        const dels = data.deletions || 0;
        const diffText = (adds > 0 || dels > 0) ? (' (+' + adds + ' -' + dels + ')') : '';
        badge.innerText = 'v' + currentVersion + diffText + ' (Updated)';
        badge.style.background = '#007acc';
        badge.style.color = '#ffffff';
        setTimeout(function() {
          badge.style.background = 'var(--bg-subtle)';
          badge.style.color = 'var(--text-muted)';
        }, 3000);
      }

      const latestVData = data.latest_version_data || (data.versions ? data.versions[currentVersion - 1] : null);
      if (latestVData) {
        let newFiles = latestVData.files || [];
        if (!newFiles.length && latestVData.content) {
          newFiles = [{ filename: data.filename, content: latestVData.content }];
        }

        projectFiles = newFiles;
        IS_MULTI_FILE = projectFiles.length > 1 || (data.filename || '').endsWith('.zip');
        IS_PREVIEWABLE = projectFiles.some(function(f) {
          const fn = (f.filename || '').toLowerCase();
          return fn.endsWith('.html') || fn.endsWith('.htm') || fn.endsWith('.svg') || fn.endsWith('.md') || fn.endsWith('.markdown');
        });

        const centerControls = document.getElementById('center-controls');
        if (centerControls) centerControls.style.display = IS_PREVIEWABLE ? 'flex' : 'none';

        initMonacoModels();
        renderFileTabs();

        if (currentEditor && projectFiles[activeFileIndex]) {
          currentEditor.setModel(projectFiles[activeFileIndex].model);
        } else if (currentEditor && projectFiles[0]) {
          activeFileIndex = 0;
          currentEditor.setModel(projectFiles[0].model);
        }

        if (IS_PREVIEWABLE) {
          updateLiveIframe();
          setViewMode(currentMode);
        } else {
          setViewMode('code');
        }
      }
    }

    function setupWebSocket() {
      if (!ARTIFACT_ID || ARTIFACT_ID === "art_0") return;
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = protocol + '//' + window.location.host + '/ws/' + ARTIFACT_ID;
      
      let ws = null;
      try {
        ws = new WebSocket(wsUrl);
      } catch (e) {
        return;
      }

      ws.onmessage = function(event) {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === 'artifact_update') {
            applyNewArtifactVersion(payload);
          }
        } catch (e) {}
      };

      ws.onclose = function() {
        setTimeout(setupWebSocket, 3000);
      };
    }

    document.addEventListener('DOMContentLoaded', function() {
      if (!IS_PREVIEWABLE) {
        setViewMode('code');
      } else {
        const firstFileName = projectFiles[0] ? projectFiles[0].filename : ARTIFACT_FILENAME;
        if (isMarkdownFile(firstFileName)) {
          setViewMode('preview');
        } else if (window.innerWidth > 768) {
          setViewMode('split');
        } else {
          setViewMode('preview');
        }
        updateLiveIframe();
      }

      setupWebSocket();
    });

    require.config({ paths: { vs: 'https://cdnjs.cloudflare.com/ajax/libs/monaco-editor/0.45.0/min/vs' } });
    require(['vs/editor/editor.main'], function () {
      monaco.editor.defineTheme('vscode-dark-plus', {
        base: 'vs-dark',
        inherit: true,
        rules: [
          { token: '', foreground: 'd4d4d4', background: '1e1e1e' },
          { token: 'comment', foreground: '6a9955', fontStyle: 'italic' },
          { token: 'keyword', foreground: '569cd6' },
          { token: 'keyword.control', foreground: 'c586c0' },
          { token: 'operator', foreground: 'd4d4d4' },
          { token: 'identifier', foreground: '9cdcfe' },
          { token: 'type', foreground: '4ec9b0' },
          { token: 'class', foreground: '4ec9b0' },
          { token: 'function', foreground: 'dcdcaa' },
          { token: 'string', foreground: 'ce9178' },
          { token: 'number', foreground: 'b5cea8' },
          { token: 'tag', foreground: '569cd6' },
          { token: 'attribute.name', foreground: '9cdcfe' },
          { token: 'attribute.value', foreground: 'ce9178' }
        ],
        colors: {
          'editor.background': '#1e1e1e',
          'editor.foreground': '#d4d4d4',
          'editor.lineHighlightBackground': '#282828',
          'editorLineNumber.foreground': '#858585',
          'editorLineNumber.activeForeground': '#c6c6c6',
          'editorIndentGuide.background': '#404040',
          'editorIndentGuide.activeBackground': '#707070',
          'editor.selectionBackground': '#264f78',
          'editorBracketHighlight.foreground1': '#ffd700',
          'editorBracketHighlight.foreground2': '#da70d6',
          'editorBracketHighlight.foreground3': '#179fff',
          'editorGutter.background': '#1e1e1e'
        }
      });

      initMonacoModels();

      if (projectFiles.length > 1) {
        renderFileTabs();
      }

      const initialModel = projectFiles[0] ? projectFiles[0].model : null;

      currentEditor = monaco.editor.create(document.getElementById('editor-container'), {
        model: initialModel,
        theme: 'vscode-dark-plus',
        automaticLayout: true,
        minimap: { enabled: false },
        fontSize: 13,
        lineHeight: 20,
        fontFamily: "'JetBrains Mono', Consolas, monospace",
        fontLigatures: true,
        bracketPairColorization: { enabled: true },
        guides: { bracketPairs: true, indentation: true },
        renderLineHighlight: 'all',
        padding: { top: 12, bottom: 12 },
        lineNumbersMinChars: 3,
        scrollBeyondLastLine: false,
        smoothScrolling: true,
        cursorBlinking: 'smooth'
      });

      if (IS_PREVIEWABLE) {
        updateLiveIframe();
      }
    });
  </script>
</body>
</html>
"""

class PlaygroundServer:
    def __init__(self, port: int = PLAYGROUND_PORT):
        self.port = port
        self.public_url: str | None = None
        self.app = web.Application()
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        self.tunnel_proc: asyncio.subprocess.Process | None = None
        self._sockets: dict[str, set[web.WebSocketResponse]] = defaultdict(set)
        self._setup_routes()

    def _setup_routes(self):
        self.app.router.add_get("/p/{artifact_id}", self.handle_playground_page)
        self.app.router.add_get("/p/{artifact_id}/{filename:.*}", self.handle_artifact_subfile)
        self.app.router.add_get("/raw/{artifact_id}", self.handle_raw_artifact)
        self.app.router.add_get("/api/artifact/{artifact_id}/versions", self.handle_artifact_versions_api)
        self.app.router.add_get("/ws/{artifact_id}", self.handle_ws)
        self.app.router.add_get("/favicon.ico", self.handle_favicon)

    def get_artifact_url(self, artifact_id: str, version: int = 1) -> str | None:
        if not self.public_url:
            return None
        return f"{self.public_url}/p/{artifact_id}?v={version}"

    async def handle_favicon(self, request: web.Request) -> web.Response:
        return web.Response(status=204)

    async def handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        artifact_id = request.match_info.get("artifact_id", "")
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)

        self._sockets[str(artifact_id)].add(ws)
        logger.debug(f"[Playground WS] Connected client for artifact {artifact_id}")

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.ERROR:
                    logger.debug(f"WS error: {ws.exception()}")
        finally:
            self._sockets[str(artifact_id)].discard(ws)
            if not self._sockets[str(artifact_id)]:
                self._sockets.pop(str(artifact_id), None)
            logger.debug(f"[Playground WS] Disconnected client for artifact {artifact_id}")

        return ws

    async def notify_artifact_updated(self, artifact_id: str, version_data: dict[str, Any]):
        sockets = list(self._sockets.get(str(artifact_id), []))
        if not sockets:
            return

        payload_obj = {
            "type": "artifact_update",
            "artifact_id": str(artifact_id),
            "filename": version_data.get("filename", ""),
            "title": version_data.get("title", ""),
            "active_version": version_data.get("active_version", 1),
            "total_versions": version_data.get("total_versions", 1),
            "additions": version_data.get("additions", 0),
            "deletions": version_data.get("deletions", 0),
            "latest_version_data": version_data.get("latest_version_data")
        }
        msg_str = json.dumps(payload_obj)

        for ws in sockets:
            try:
                if not ws.closed:
                    await ws.send_str(msg_str)
            except Exception:
                pass
        logger.info(f"[Playground WS] Pushed v{version_data.get('active_version')} update to {len(sockets)} connected client(s).")

    async def handle_artifact_versions_api(self, request: web.Request) -> web.Response:
        artifact_id = request.match_info.get("artifact_id", "")
        art_data = branch_manager.get_artifact(artifact_id)
        if not art_data:
            return web.json_response({"error": "Artifact not found"}, status=404)

        versions = art_data.get("versions", [])
        return web.json_response({
            "artifact_id": artifact_id,
            "filename": art_data.get("filename", "artifact.txt"),
            "title": art_data.get("title", "Artifact"),
            "active_version": art_data.get("active_version", len(versions)),
            "latest_version": len(versions),
            "total_versions": len(versions),
            "versions": versions
        })

    async def handle_artifact_subfile(self, request: web.Request) -> web.Response:
        artifact_id = request.match_info.get("artifact_id", "")
        req_filename = request.match_info.get("filename", "")
        v_param = request.query.get("v", "latest")

        art_data = branch_manager.get_artifact(artifact_id)
        if not art_data:
            return web.Response(text="Artifact not found", status=404)

        versions = art_data.get("versions", [])
        total_v = len(versions)
        target_v = int(v_param) if (v_param.isdigit() and 1 <= int(v_param) <= total_v) else total_v
        target_v_data = versions[target_v - 1] if (1 <= target_v <= len(versions)) else (versions[-1] if versions else {})

        files = target_v_data.get("files", [])
        clean_req = req_filename.strip().lower()

        for f in files:
            f_name = f.get("filename", "")
            if f_name.strip().lower() == clean_req or f_name.strip().lower().endswith("/" + clean_req):
                content = f.get("content", "")
                content_type = "text/plain"
                if clean_req.endswith(".js"):
                    content_type = "application/javascript"
                elif clean_req.endswith(".css"):
                    content_type = "text/css"
                elif clean_req.endswith(".html") or clean_req.endswith(".htm"):
                    content_type = "text/html"
                elif clean_req.endswith(".json"):
                    content_type = "application/json"
                elif clean_req.endswith(".svg"):
                    content_type = "image/svg+xml"

                return web.Response(text=content, content_type=content_type)

        return web.Response(text=f"File '{req_filename}' not found in artifact", status=404)

    async def handle_playground_page(self, request: web.Request) -> web.Response:
        artifact_id = request.match_info.get("artifact_id", "")
        v_param = request.query.get("v", "latest")

        art_data = branch_manager.get_artifact(artifact_id)
        if not art_data:
            return web.Response(text="Artifact not found or expired.", status=404)

        versions = art_data.get("versions", [])
        total_v = len(versions)
        
        if v_param == "latest" or not v_param.isdigit():
            target_v = total_v if total_v > 0 else 1
        else:
            target_v = int(v_param)

        target_v_data = versions[target_v - 1] if (1 <= target_v <= len(versions)) else (versions[-1] if versions else {})

        filename = art_data.get("filename", "artifact.txt")
        files = target_v_data.get("files", [])

        if not files:
            content = target_v_data.get("content", "")
            files = [{"filename": filename, "content": content}]

        is_previewable = any(f.get("filename", "").lower().endswith((".html", ".htm", ".svg", ".md", ".markdown")) for f in files)

        adds = target_v_data.get("additions", 0)
        dels = target_v_data.get("deletions", 0)
        diff_badge = f"(+{adds} -{dels})" if (adds > 0 or dels > 0) else ""

        raw_files_b64 = base64.b64encode(json.dumps(files).encode("utf-8")).decode("utf-8")

        html = PLAYGROUND_HTML_TEMPLATE
        html = html.replace("{{ARTIFACT_ID}}", artifact_id)
        html = html.replace("{{FILENAME}}", filename)
        html = html.replace("{{VERSION}}", str(target_v))
        html = html.replace("{{DIFF_BADGE}}", diff_badge)
        html = html.replace("{{IS_PREVIEWABLE_BOOL}}", "true" if is_previewable else "false")
        html = html.replace("{{RAW_FILES_B64}}", raw_files_b64)
        
        has_multiple_files = len(files) > 1 or filename.endswith(".zip")
        html = html.replace("{{TABS_BAR_DISPLAY}}", "flex" if has_multiple_files else "none")
        html = html.replace("{{CENTER_CONTROLS_DISPLAY}}", "flex" if is_previewable else "none")
        html = html.replace("{{RIGHT_PANE_DISPLAY}}", "flex" if is_previewable else "none")
        html = html.replace("{{DIVIDER_DISPLAY}}", "block" if is_previewable else "none")

        return web.Response(text=html, content_type="text/html")

    async def handle_raw_artifact(self, request: web.Request) -> web.Response:
        artifact_id = request.match_info.get("artifact_id", "")
        v_param = request.query.get("v", "1")
        target_v = int(v_param) if v_param.isdigit() else 1

        art_data = branch_manager.get_artifact(artifact_id)
        if not art_data:
            return web.Response(text="Not found", status=404)

        versions = art_data.get("versions", [])
        target_v_data = versions[target_v - 1] if (1 <= target_v <= len(versions)) else (versions[-1] if versions else {})
        content = target_v_data.get("content", "")

        return web.Response(text=content, content_type="text/plain")

    async def start(self):
        try:
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, "127.0.0.1", self.port)
            await self.site.start()
            logger.info(f"[Playground Server] Local HTTP server listening on http://127.0.0.1:{self.port}")
            
            asyncio.create_task(self._start_tunnel())
        except Exception as e:
            logger.warning(f"[Playground Server] Failed to start local HTTP server: {e}")

    async def _start_tunnel(self):
        has_native = shutil.which("cloudflared") is not None
        has_docker = shutil.which("docker") is not None

        if not has_native and not has_docker:
            logger.warning("[Playground Server] Neither 'cloudflared' nor 'docker' found. Live tunnel disabled.")
            return

        cmd = []
        if has_native:
            logger.info("[Playground Server] Starting tunnel using native cloudflared binary...")
            cmd = ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{self.port}"]
        else:
            logger.info("[Playground Server] Cleaning old tunnel containers and starting Docker cloudflared...")
            try:
                cleanup_proc = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", "priesty_cf_tunnel",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL
                )
                await cleanup_proc.communicate()
            except Exception:
                pass

            target_host = "http://host.docker.internal" if sys.platform in ("win32", "darwin") else "http://127.0.0.1"
            cmd = [
                "docker", "run", "--name", "priesty_cf_tunnel", "--rm",
                "cloudflare/cloudflared:latest",
                "tunnel", "--url", f"{target_host}:{self.port}"
            ]

        try:
            self.tunnel_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            while True:
                line_bytes = await self.tunnel_proc.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace")
                
                match = re.search(r'https:\/\/[a-zA-Z0-9-]+\.trycloudflare\.com', line)
                if match:
                    self.public_url = match.group(0)
                    logger.info(f"✨ [Playground Server] Live Tunnel Online: {self.public_url}")
                    break

            async def drain():
                try:
                    while True:
                        line = await self.tunnel_proc.stdout.readline()
                        if not line:
                            break
                except Exception:
                    pass

            asyncio.create_task(drain())

        except Exception as e:
            logger.warning(f"[Playground Server] Cloudflare Tunnel failed: {e}")

    async def stop(self):
        if self.tunnel_proc:
            try:
                self.tunnel_proc.terminate()
            except Exception:
                pass
        if shutil.which("docker"):
            try:
                kill_proc = await asyncio.create_subprocess_exec("docker", "rm", "-f", "priesty_cf_tunnel")
                await kill_proc.communicate()
            except Exception:
                pass
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()

playground_server = PlaygroundServer()