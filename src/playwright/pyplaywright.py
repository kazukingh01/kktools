#!/usr/bin/env python
# -*- coding: utf-8 -*-

import base64
import json
from pathlib import Path
from typing import List, Dict, Any, Union, Tuple
import urllib.parse
import textwrap
from playwright.sync_api import sync_playwright

FONT_FILE_NAME   = "ipaexg.ttf"
FONT_FAMILY_NAME = "IPAexGothic"


def _load_font_as_data_uri(path: Path) -> str:
    """Return data URI for the given font file."""
    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:font/ttf;base64,{b64}"


def build_font_css() -> Tuple[str, int]:
    """
    Build @font-face CSS for the IPAex Gothic font bundled under ./fonts/ipaexg.ttf.
    """
    base_dir = Path(__file__).resolve().parent
    fonts_dir = base_dir / "fonts"
    font_path = fonts_dir / FONT_FILE_NAME

    if not font_path.exists():
        return "", 0

    try:
        data_uri = _load_font_as_data_uri(font_path)
    except OSError as exc:
        print(f"[warn] failed to load font {font_path}: {exc}")
        return "", 0
    font_definitions = f"""@font-face {{
  font-family: '{FONT_FAMILY_NAME}';
  font-style: normal;
  font-weight: 400;
  font-display: swap;
  src: url({data_uri}) format('truetype');
}}

body,
button,
input,
select,
textarea {{
  font-family: '{FONT_FAMILY_NAME}', sans-serif;
}}"""
    return font_definitions, 1


FONT_CSS, FONT_FACE_COUNT = build_font_css()


def run_actions_on_html(
    html_path: Union[Path, str],
    actions: List[Dict[str, Any]],
    viewport=(1080, 2400),
    device_scale_factor=3,
    is_head: bool=False,
    is_no_sandbox: bool=False
):
    if FONT_FACE_COUNT == 0:
        print(f"[warn] font ./fonts/{FONT_FILE_NAME} not found; falling back to default fonts.")
    if isinstance(html_path, str) and html_path.startswith("data:"):
        url = html_path
    else:
        url = Path(html_path).resolve().as_uri()

    with sync_playwright() as p:
        browser = None
        engine_used = None
        launch_errors = []

        # Prefer chromium, but fall back to firefox if sandbox restrictions block launch.
        for engine in ("chromium", "firefox"):
            try:
                if engine == "chromium":
                    browser = p.chromium.launch(
                        headless=(is_head == False),
                        chromium_sandbox=False,
                        args=[
                            "--single-process",
                            "--no-zygote",
                            "--disable-gpu",
                        ] + (["--no-sandbox", "--disable-setuid-sandbox"] if is_no_sandbox else []),
                    )
                else:
                    browser = p.firefox.launch(headless=(is_head == False))
                engine_used = engine
                break
            except Exception as exc:
                launch_errors.append((engine, str(exc)))
                continue

        if browser is None:
            raise RuntimeError(f"Browser launch failed: {launch_errors}")

        context = (
            browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]},
                device_scale_factor=device_scale_factor,
                is_mobile=True,
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 14; Pixel 7 Pro) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/118.0.0.0 Mobile Safari/537.36"
                ),
            )
            if engine_used == "chromium"
            else browser.new_context(
                viewport={"width": viewport[0], "height": viewport[1]},
                is_mobile=True,
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 14; Pixel 7 Pro; rv:118.0) "
                    "Gecko/20100101 Firefox/118.0"
                ),
            )
        )

        # Inject font CSS as early as possible so the initial render uses it.
        if FONT_CSS:
            print(f"[info] injecting custom font css ({FONT_FACE_COUNT} face(s))")
            context.add_init_script(
                f"""
                (() => {{
                  const style = document.createElement('style');
                  style.textContent = {json.dumps(FONT_CSS)};
                  document.documentElement.appendChild(style);
                }})();
                """
            )

        page = context.new_page()
        page.goto(url)
        page.wait_for_load_state("networkidle")

        # Append style again so it wins cascade order over in-document styles
        if FONT_CSS:
            try:
                page.add_style_tag(content=FONT_CSS)
            except Exception as exc:
                print(f"[warn] page-level font css injection failed: {exc}")

        # Ensure fonts are ready before taking screenshots
        if FONT_CSS:
            font_check_value = f'12px "{FONT_FAMILY_NAME}"'
            font_check_value_json = json.dumps(font_check_value)
            try:
                page.wait_for_function(
                    "() => document.fonts && document.fonts.status === 'loaded'",
                    timeout=5000,
                )
            except Exception:
                try:
                    page.wait_for_function(
                        f"() => document.fonts && document.fonts.check({font_check_value_json})",
                        timeout=5000,
                    )
                except Exception as exc:
                    print(f"[warn] font readiness wait failed: {exc}")
            try:
                ok = page.evaluate(
                    f"() => document.fonts ? document.fonts.check({font_check_value_json}) : false"
                )
                print(f"[info] font check '{FONT_FAMILY_NAME}': {ok}")
            except Exception as exc:
                print(f"[warn] font check evaluation failed: {exc}")

        for i, action in enumerate(actions):
            kind = action.get("action")
            print(f"[{i}] do: {kind} -> {action}")
            if kind == "click":
                selector = action["selector"]
                page.click(selector)
            elif kind == "scroll":
                target = action.get("target", "window")
                x = action.get("x", 0)
                y = action.get("y", 0)
                delta = {"x": x, "y": y}
                if target == "window":
                    # ウィンドウ全体をスクロール
                    page.evaluate("(offset) => window.scrollBy(offset.x, offset.y)", delta)
                else:
                    # 特定要素内をスクロール
                    sel = target
                    page.eval_on_selector(
                        sel,
                        "(el, offset) => { el.scrollBy(offset.x, offset.y); }",
                        arg=delta,
                    )
            elif kind == "wait":
                ms = action.get("ms", 500)
                page.wait_for_timeout(ms)
            elif kind == "type":
                selector = action["selector"]
                text = action["text"]
                clear = action.get("clear", True)
                if clear:
                    page.fill(selector, "")
                page.type(selector, text)
            elif kind == "screenshot":
                path = action.get("path", f"shot_{i:03}.png")
                full_page = action.get("full_page", True)
                page.screenshot(path=path, full_page=full_page)
            else:
                print(f"unknown action: {kind}")
        context.close()
        browser.close()

def test_html():
    return """
<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <title>サンプル商品ページ</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      display: flex;
      flex-direction: column;
      height: 100vh;
      color: #222;
      background: #f5f5f7;
    }

    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      background: #111827;
      color: #f9fafb;
      padding: 0.5rem 1rem;
      position: relative;
      z-index: 100; /* 右サイドバーより上に出す */
    }

    .logo {
      font-weight: bold;
      font-size: 1.1rem;
    }

    .header-right {
      display: flex;
      gap: 0.75rem;
      align-items: center;
    }

    .header-button {
      background: #2563eb;
      border: none;
      color: white;
      padding: 0.35rem 0.8rem;
      border-radius: 9999px;
      font-size: 0.85rem;
      cursor: pointer;
    }

    .header-button.secondary {
      background: transparent;
      border: 1px solid #4b5563;
      color: #e5e7eb;
    }

    .header-button:hover {
      opacity: 0.9;
    }

    .hamburger {
      display: none;
      cursor: pointer;
      font-size: 1.5rem;
      line-height: 1;
      padding: 0.25rem 0.5rem;
      border-radius: 0.375rem;
    }

    .hamburger:hover {
      background: rgba(249, 250, 251, 0.1);
    }

    .layout {
      flex: 1 1 auto;
      display: flex;
      overflow: hidden; /* サイドバー＋メイン全体のはみ出し抑制 */
    }

    aside.sidebar {
      width: 260px;
      background: #111827;
      color: #e5e7eb;
      padding: 1rem;
      overflow-y: auto; /* サイドバー自身も縦スクロール可能 */
    }

    .sidebar h2 {
      font-size: 0.9rem;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #9ca3af;
      margin-top: 0;
      margin-bottom: 0.75rem;
    }

    .sidebar-nav {
      list-style: none;
      padding: 0;
      margin: 0;
    }

    .sidebar-nav li {
      margin-bottom: 0.35rem;
    }

    .sidebar-nav a {
      display: block;
      padding: 0.45rem 0.5rem;
      border-radius: 0.375rem;
      text-decoration: none;
      color: #e5e7eb;
      font-size: 0.9rem;
    }

    .sidebar-nav a:hover,
    .sidebar-nav a.active {
      background: #1f2937;
    }

    .sidebar-section {
      margin-top: 1.25rem;
    }

    .sidebar-filter-label {
      font-size: 0.8rem;
      color: #9ca3af;
      margin-bottom: 0.25rem;
    }

    .sidebar input[type="checkbox"] {
      margin-right: 0.4rem;
    }

    main.main-content {
      flex: 1 1 auto;
      padding: 1rem 1.5rem;
      overflow-y: auto; /* メインを縦スクロール可能に */
      background: radial-gradient(circle at top left, #eff6ff 0, #f5f5f7 40%);
    }

    .breadcrumbs {
      font-size: 0.8rem;
      color: #6b7280;
      margin-bottom: 0.3rem;
    }

    .page-title {
      margin: 0;
      font-size: 1.4rem;
      font-weight: 600;
    }

    .page-subtitle {
      margin: 0.25rem 0 1rem;
      font-size: 0.9rem;
      color: #4b5563;
    }

    .toolbar {
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      align-items: center;
      margin-bottom: 1rem;
    }

    .toolbar-left,
    .toolbar-right {
      display: flex;
      gap: 0.5rem;
      align-items: center;
    }

    .toolbar-right {
      margin-left: auto;
    }

    .button {
      border-radius: 9999px;
      border: 1px solid #d1d5db;
      background: white;
      padding: 0.35rem 0.8rem;
      font-size: 0.8rem;
      cursor: pointer;
    }

    .button.primary {
      background: #2563eb;
      color: white;
      border-color: #2563eb;
    }

    .button.danger {
      background: #dc2626;
      color: #fef2f2;
      border-color: #b91c1c;
    }

    .button:hover {
      filter: brightness(0.97);
    }

    .search-input {
      border-radius: 9999px;
      border: 1px solid #d1d5db;
      padding: 0.35rem 0.8rem;
      font-size: 0.8rem;
    }

    .content-grid {
      display: grid;
      grid-template-columns: 2fr 3fr;
      gap: 1rem;
      margin-bottom: 1rem;
    }

    .card {
      background: rgba(255, 255, 255, 0.9);
      border-radius: 0.75rem;
      padding: 1rem;
      box-shadow: 0 12px 22px rgba(15, 23, 42, 0.12);
    }

    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 0.75rem;
    }

    .card-title {
      margin: 0;
      font-size: 1rem;
      font-weight: 600;
    }

    .card-subtitle {
      margin: 0.25rem 0 0;
      font-size: 0.8rem;
      color: #6b7280;
    }

    .price {
      font-size: 1.3rem;
      font-weight: bold;
      color: #dc2626;
    }

    .price span {
      font-size: 0.8rem;
      font-weight: 400;
      color: #6b7280;
      margin-left: 0.3rem;
    }

    .badge {
      display: inline-block;
      padding: 0.15rem 0.5rem;
      border-radius: 9999px;
      background: #fee2e2;
      color: #b91c1c;
      font-size: 0.7rem;
      font-weight: 500;
      margin-right: 0.25rem;
    }

    .product-actions {
      display: flex;
      gap: 0.5rem;
      margin-top: 0.75rem;
    }

    .product-detail-list {
      list-style: none;
      padding: 0;
      margin: 0.5rem 0 0;
      font-size: 0.85rem;
      color: #4b5563;
    }

    .product-detail-list li {
      margin-bottom: 0.25rem;
    }

    .pill-list {
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      margin-top: 0.5rem;
    }

    .pill {
      padding: 0.2rem 0.6rem;
      border-radius: 9999px;
      border: 1px solid #d1d5db;
      background: #f9fafb;
      font-size: 0.75rem;
    }

    .product-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
      gap: 0.75rem;
      margin-bottom: 1.5rem;
    }

    .product-card {
      background: white;
      border-radius: 0.75rem;
      padding: 0.75rem;
      border: 1px solid #e5e7eb;
      display: flex;
      flex-direction: column;
      gap: 0.4rem;
    }

    .product-name {
      font-size: 0.9rem;
      font-weight: 500;
    }

    .product-meta {
      font-size: 0.75rem;
      color: #6b7280;
    }

    .product-card-footer {
      margin-top: auto;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 0.5rem;
      font-size: 0.75rem;
    }

    .product-small-price {
      font-weight: 600;
    }

    .table-card {
      margin-bottom: 1.5rem;
    }

    .table-wrapper {
      width: 100%;
      overflow-x: auto; /* 横スクロール */
      margin-top: 0.5rem;
    }

    table {
      border-collapse: collapse;
      min-width: 900px; /* 敢えて広くして横スクロールを発生させる */
      font-size: 0.8rem;
    }

    th, td {
      border: 1px solid #e5e7eb;
      padding: 0.4rem 0.5rem;
      white-space: nowrap;
      text-align: left;
    }

    th {
      background: #f9fafb;
      position: sticky;
      top: 0;
      z-index: 1;
    }

    tr:nth-child(even) td {
      background: #fdfdfd;
    }

    footer {
      background: #111827;
      color: #6b7280;
      font-size: 0.75rem;
      padding: 0.5rem 1rem;
      text-align: center;
    }

    /* ▼ ここから右サイドバー用のCSS ▼ */

    .right-sidebar {
      position: fixed;
      top: 0;
      right: 0;
      bottom: 0;
      width: 320px;
      background: #f3f4f6;
      border-left: 1px solid #e5e7eb;
      box-shadow: -6px 0 16px rgba(15, 23, 42, 0.25);
      padding: 1rem 1.25rem;
      overflow-y: auto;
      transform: translateX(100%); /* 初期状態：画面外 */
      transition: transform 0.25s ease-out;
      z-index: 60;
    }

    .right-sidebar.open {
      transform: translateX(0); /* 画面内にスライドイン */
    }

    .right-sidebar-title {
      font-size: 0.95rem;
      font-weight: 600;
      margin: 0 0 0.75rem;
    }

    .right-sidebar-section {
      margin-bottom: 1rem;
      padding-bottom: 0.75rem;
      border-bottom: 1px solid #e5e7eb;
      font-size: 0.85rem;
    }

    .right-sidebar-label {
      font-size: 0.78rem;
      color: #6b7280;
      margin-bottom: 0.3rem;
    }

    .right-sidebar select,
    .right-sidebar input[type="text"] {
      width: 100%;
      padding: 0.4rem 0.55rem;
      font-size: 0.85rem;
      border-radius: 0.5rem;
      border: 1px solid #d1d5db;
      background: #ffffff;
    }

    .right-sidebar-checkbox {
      display: flex;
      align-items: center;
      gap: 0.4rem;
      margin: 0.15rem 0;
      font-size: 0.85rem;
    }

    .right-sidebar-footer-text {
      font-size: 0.75rem;
      color: #6b7280;
      margin-top: 0.3rem;
    }

    .right-sidebar-button {
      width: 100%;
      margin-top: 0.6rem;
      padding: 0.5rem 0;
      font-size: 0.85rem;
      border-radius: 9999px;
      border: none;
      background: #2563eb;
      color: #ffffff;
      cursor: pointer;
    }

    .right-sidebar-button.secondary {
      background: #ffffff;
      color: #111827;
      border: 1px solid #d1d5db;
      margin-top: 0.4rem;
    }

    .right-sidebar-button:hover {
      filter: brightness(0.97);
    }

    /* サイドバーのハンドル（» / «） */
    .right-sidebar-handle {
      position: fixed;
      top: 50%;
      right: 0;
      transform: translate(50%, -50%);
      width: 32px;
      height: 48px;
      border-radius: 9999px 0 0 9999px;
      border: 1px solid #d1d5db;
      background: #ffffff;
      box-shadow: 0 4px 10px rgba(15, 23, 42, 0.3);
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      z-index: 61;
      font-size: 1.1rem;
    }

    /* ▲ 右サイドバー用のCSS ここまで ▲ */

    /* レスポンシブ（スマホ時の左サイドバー挙動） */
    @media (max-width: 900px) {
      .layout {
        position: relative;
      }

      aside.sidebar {
        position: fixed;
        left: -280px;
        top: 0;
        bottom: 0;
        z-index: 50;
        transition: left 0.25s ease-out;
      }

      aside.sidebar.open {
        left: 0;
      }

      .hamburger {
        display: inline-block;
      }

      .header-right {
        display: none;
      }

      .content-grid {
        grid-template-columns: 1fr;
      }
    }
  </style>
</head>
<body>
  <header>
    <div class="left">
      <span class="hamburger" onclick="toggleSidebar()">☰</span>
      <span class="logo">ShopSample</span>
    </div>
    <div class="header-right">
      <button class="header-button secondary">サインイン</button>
      <button class="header-button">カートを見る</button>
    </div>
  </header>

  <div class="layout">
    <aside class="sidebar" id="sidebar">
      <h2>カテゴリ</h2>
      <ul class="sidebar-nav">
        <li><a href="#" class="active">すべての商品</a></li>
        <li><a href="#">ノートPC</a></li>
        <li><a href="#">タブレット</a></li>
        <li><a href="#">スマートフォン</a></li>
        <li><a href="#">アクセサリー</a></li>
        <li><a href="#">在庫限りセール</a></li>
      </ul>

      <div class="sidebar-section">
        <div class="sidebar-filter-label">価格帯</div>
        <label><input type="checkbox" /> 〜 ¥50,000</label><br />
        <label><input type="checkbox" /> ¥50,000〜¥100,000</label><br />
        <label><input type="checkbox" /> ¥100,000〜</label>
      </div>

      <div class="sidebar-section">
        <div class="sidebar-filter-label">在庫状況</div>
        <label><input type="checkbox" checked /> 在庫あり</label><br />
        <label><input type="checkbox" /> 予約受付中</label>
      </div>
    </aside>

    <main class="main-content">
      <!-- ページタイトル部分 -->
      <div class="breadcrumbs">ホーム &gt; ノートPC &gt; ビジネス向け</div>
      <h1 class="page-title">ビジネス向けノートPC</h1>
      <p class="page-subtitle">軽量・長時間バッテリー・高耐久。リモートワークや出張に最適なモデルを厳選しています。</p>

      <!-- ツールバー（ボタンいろいろ） -->
      <div class="toolbar">
        <div class="toolbar-left">
          <button class="button primary">新規商品を追加</button>
          <button class="button">並び替え：人気順</button>
          <button class="button">並び替え：価格</button>
        </div>
        <div class="toolbar-right">
          <input class="search-input" type="text" placeholder="商品名・型番で検索" />
          <button class="button">検索</button>
          <button class="button danger">セール対象に設定</button>
        </div>
      </div>

      <!-- 上部2カラム（縦スクロールされる領域） -->
      <div class="content-grid">
        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">ピックアップ商品</h2>
              <p class="card-subtitle">テレワーク向けのバランスモデル</p>
            </div>
            <div class="price">¥129,800 <span>税込・送料無料</span></div>
          </div>

          <div>
            <span class="badge">限定セール</span>
            <span class="badge" style="background:#dcfce7;color:#166534;">在庫あり</span>
          </div>

          <ul class="product-detail-list">
            <li>14インチ / 重量 1.1kg / バッテリー最長 18時間</li>
            <li>第13世代 Core i7 / メモリ 16GB / SSD 512GB</li>
            <li>Thunderbolt 4 / Wi-Fi 6E / 指紋認証</li>
          </ul>

          <div class="pill-list">
            <span class="pill">フルHD</span>
            <span class="pill">ビジネス</span>
            <span class="pill">オンライン会議向け</span>
            <span class="pill">3年保証</span>
          </div>

          <div class="product-actions">
            <button class="button primary">カートに追加</button>
            <button class="button">お気に入りに追加</button>
            <button class="button">詳細を見る</button>
          </div>
        </section>

        <section class="card">
          <div class="card-header">
            <div>
              <h2 class="card-title">在庫サマリー</h2>
              <p class="card-subtitle">カテゴリ別の在庫・販売状況</p>
            </div>
          </div>
          <ul class="product-detail-list">
            <li>ビジネス向けノートPC：在庫 <strong>124台</strong> / 本日販売 <strong>18台</strong></li>
            <li>クリエイター向けノートPC：在庫 <strong>54台</strong> / 本日販売 <strong>8台</strong></li>
            <li>エントリー向けノートPC：在庫 <strong>203台</strong> / 本日販売 <strong>27台</strong></li>
          </ul>
          <div class="pill-list">
            <span class="pill">在庫補充アラート</span>
            <span class="pill">売れ筋ランキング</span>
            <span class="pill">直近7日間の傾向</span>
          </div>
        </section>
      </div>

      <!-- 商品カード一覧 -->
      <section class="card">
        <div class="card-header">
          <h2 class="card-title">商品一覧</h2>
          <span class="card-subtitle">ビジネス向けノートPCの代表的なラインナップ</span>
        </div>

        <div class="product-grid">
          <div class="product-card">
            <div class="product-name">BizBook 14 Pro</div>
            <div class="product-meta">Core i7 / 16GB / 512GB SSD / 1.1kg</div>
            <div class="product-card-footer">
              <span class="product-small-price">¥129,800</span>
              <button class="button primary">カートに追加</button>
            </div>
          </div>
          <div class="product-card">
            <div class="product-name">BizBook 13 Air</div>
            <div class="product-meta">Core i5 / 8GB / 256GB SSD / 0.99kg</div>
            <div class="product-card-footer">
              <span class="product-small-price">¥99,800</span>
              <button class="button">詳細</button>
            </div>
          </div>
          <div class="product-card">
            <div class="product-name">BizBook 15 Plus</div>
            <div class="product-meta">Ryzen 7 / 16GB / 1TB SSD / 1.3kg</div>
            <div class="product-card-footer">
              <span class="product-small-price">¥139,800</span>
              <button class="button primary">カートに追加</button>
            </div>
          </div>
          <div class="product-card">
            <div class="product-name">BizBook 14 Basic</div>
            <div class="product-meta">Core i3 / 8GB / 256GB SSD / 1.2kg</div>
            <div class="product-card-footer">
              <span class="product-small-price">¥79,800</span>
              <button class="button">詳細</button>
            </div>
          </div>
          <div class="product-card">
            <div class="product-name">BizBook 16 Creator</div>
            <div class="product-meta">Core i9 / 32GB / 1TB SSD / RTX搭載</div>
            <div class="product-card-footer">
              <span class="product-small-price">¥199,800</span>
              <button class="button primary">カートに追加</button>
            </div>
          </div>
          <div class="product-card">
            <div class="product-name">BizBook 14 Travel</div>
            <div class="product-meta">Ryzen 5 / 16GB / 512GB SSD / 1.05kg</div>
            <div class="product-card-footer">
              <span class="product-small-price">¥114,800</span>
              <button class="button">詳細</button>
            </div>
          </div>
        </div>
      </section>

      <!-- 横スクロールテーブル -->
      <section class="card table-card">
        <div class="card-header">
          <h2 class="card-title">商品スペック比較</h2>
          <span class="card-subtitle">列数を増やしてあえて横スクロールさせるテーブル例</span>
        </div>
        <div class="table-wrapper">
          <table>
            <thead>
              <tr>
                <th>商品名</th>
                <th>CPU</th>
                <th>メモリ</th>
                <th>ストレージ</th>
                <th>画面サイズ</th>
                <th>解像度</th>
                <th>重量</th>
                <th>バッテリー</th>
                <th>OS</th>
                <th>Thunderbolt</th>
                <th>USBポート</th>
                <th>LAN</th>
                <th>Wi-Fi</th>
                <th>Bluetooth</th>
                <th>保証</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>BizBook 14 Pro</td>
                <td>Core i7-1360P</td>
                <td>16GB</td>
                <td>512GB SSD</td>
                <td>14"</td>
                <td>1920x1080</td>
                <td>1.1kg</td>
                <td>最大18時間</td>
                <td>Windows 11 Pro</td>
                <td>あり</td>
                <td>USB-C x2, USB-A x2</td>
                <td>なし</td>
                <td>Wi-Fi 6E</td>
                <td>5.3</td>
                <td>3年</td>
              </tr>
              <tr>
                <td>BizBook 13 Air</td>
                <td>Core i5-1335U</td>
                <td>8GB</td>
                <td>256GB SSD</td>
                <td>13.3"</td>
                <td>1920x1200</td>
                <td>0.99kg</td>
                <td>最大16時間</td>
                <td>Windows 11 Home</td>
                <td>あり</td>
                <td>USB-C x2</td>
                <td>なし</td>
                <td>Wi-Fi 6</td>
                <td>5.2</td>
                <td>1年</td>
              </tr>
              <tr>
                <td>BizBook 15 Plus</td>
                <td>Ryzen 7 7840U</td>
                <td>16GB</td>
                <td>1TB SSD</td>
                <td>15.6"</td>
                <td>2560x1440</td>
                <td>1.3kg</td>
                <td>最大14時間</td>
                <td>Windows 11 Pro</td>
                <td>なし</td>
                <td>USB-C x1, USB-A x3</td>
                <td>RJ45</td>
                <td>Wi-Fi 6E</td>
                <td>5.3</td>
                <td>2年</td>
              </tr>
              <tr>
                <td>BizBook 16 Creator</td>
                <td>Core i9-13900H</td>
                <td>32GB</td>
                <td>1TB SSD</td>
                <td>16"</td>
                <td>3840x2400</td>
                <td>1.6kg</td>
                <td>最大10時間</td>
                <td>Windows 11 Pro</td>
                <td>あり</td>
                <td>USB-C x2, USB-A x2</td>
                <td>RJ45</td>
                <td>Wi-Fi 6E</td>
                <td>5.3</td>
                <td>3年</td>
              </tr>
              <tr>
                <td>BizBook 14 Basic</td>
                <td>Core i3-1315U</td>
                <td>8GB</td>
                <td>256GB SSD</td>
                <td>14"</td>
                <td>1920x1080</td>
                <td>1.2kg</td>
                <td>最大12時間</td>
                <td>Windows 11 Home</td>
                <td>なし</td>
                <td>USB-C x1, USB-A x2</td>
                <td>なし</td>
                <td>Wi-Fi 6</td>
                <td>5.2</td>
                <td>1年</td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
    </main>
  </div>

  <!-- ▼ 右から出てくるサイドバーとハンドル ▼ -->
  <aside class="right-sidebar" id="right-sidebar">
    <h2 class="right-sidebar-title">フィルター</h2>

    <div class="right-sidebar-section">
      <div class="right-sidebar-label">対象月度</div>
      <select>
        <option>2025年12月度</option>
        <option>2025年11月度</option>
        <option>2025年10月度</option>
      </select>
    </div>

    <div class="right-sidebar-section">
      <div class="right-sidebar-label">ブランド</div>
      <label class="right-sidebar-checkbox">
        <input type="checkbox" checked />
        pilates K
      </label>
      <label class="right-sidebar-checkbox">
        <input type="checkbox" checked />
        pilates K_smart
      </label>
      <div class="right-sidebar-footer-text">選択中: 2/2 ブランド</div>
    </div>

    <div class="right-sidebar-section">
      <div class="right-sidebar-label">エリア</div>
      <label class="right-sidebar-checkbox">
        <input type="checkbox" checked />
        首都圏
      </label>
      <label class="right-sidebar-checkbox">
        <input type="checkbox" checked />
        関西
      </label>
      <label class="right-sidebar-checkbox">
        <input type="checkbox" checked />
        その他
      </label>
      <div class="right-sidebar-footer-text">選択中: 3/3 エリア</div>
    </div>

    <div class="right-sidebar-section">
      <div class="right-sidebar-label">キーワード</div>
      <input type="text" placeholder="店舗名やキャンペーン名で絞り込み" />
    </div>

    <button class="right-sidebar-button">この条件で集計</button>
    <button class="right-sidebar-button secondary">条件をリセット</button>
  </aside>

  <button
    class="right-sidebar-handle"
    id="right-sidebar-handle"
    type="button"
    aria-label="右サイドバーを開閉"
    onclick="toggleRightSidebar()"
  >
    «
  </button>
  <!-- ▲ 右サイドバーここまで ▲ -->

  <footer>
    &copy; 2025 ShopSample Inc. すべての商標は各社に帰属します。
  </footer>

  <script>
    // 左のカテゴリサイドバー（既存）
    function toggleSidebar() {
      var sidebar = document.getElementById('sidebar');
      sidebar.classList.toggle('open');
    }

    // 右から出てくるサイドバー
    function toggleRightSidebar() {
      var rightSidebar = document.getElementById('right-sidebar');
      var handle = document.getElementById('right-sidebar-handle');
      var isOpen = rightSidebar.classList.toggle('open');

      if (handle) {
        // 開いているときは「閉じる向き」、閉じているときは「開く向き」
        handle.textContent = isOpen ? '»' : '«';
        handle.style.right = isOpen ? '320px' : '0';
      }
    }
  </script>
</body>
</html>
""".strip()

def html_string_to_data_uri(html: str) -> str:
    quoted = urllib.parse.quote(html)
    return f"data:text/html;charset=utf-8,{quoted}"

if __name__ == "__main__":
    import argparse

    default_actions = [{"action": "wait", "ms": 10000}, ]

    action_description = textwrap.dedent(
        """\
        Actions JSON の書き方（selector は .class でも #id でもOK）:
          - wait: {"action":"wait","ms":500}
          - click: {"action":"click","selector":".btn"}
          - scroll: {"action":"scroll","target":"#main-content","x":0,"y":800}  # target はスクロールさせたい要素の CSS セレクタ。window 指定は不可
          - type: {"action":"type","selector":"input[name=q]","text":"hello","clear":true}
          - screenshot: {"action":"screenshot","path":"shot.png","full_page":false}

        例（組み込みデモ HTML 向け。セレクタは .class / #id のどちらでも指定可）:
          pyplaywright -a '[{"action":"wait","ms":1000},{"action":"screenshot","path":"01_initial.png","full_page":false}]'
          pyplaywright -a '[{"action":"wait","ms":1000},{"action":"click","selector":".hamburger"},{"action":"wait","ms":500},{"action":"screenshot","path":"02_left_sidebar.png","full_page":false}]'
          pyplaywright -a '[{"action":"wait","ms":1000},{"action":"click","selector":"#right-sidebar-handle"},{"action":"wait","ms":500},{"action":"screenshot","path":"03_right_sidebar_open.png","full_page":false}]'
          pyplaywright -a '[{"action":"wait","ms":1000},{"action":"scroll","target":".main-content","x":0,"y":800},{"action":"wait","ms":800},{"action":"screenshot","path":"04_scrolled_down.png","full_page":false}]'
          pyplaywright -a '[{"action":"wait","ms":1000},{"action":"scroll","target":".main-content","x":0,"y":3000},{"action":"scroll","target":".table-wrapper","x":600,"y":0},{"action":"wait","ms":800},{"action":"screenshot","path":"05_table_scrolled_right.png","full_page":false}]'
        """
    )

    parser = argparse.ArgumentParser(
        description=(
            "Playwright で HTML をモバイル相当の環境に読み込み、"
            "JSON で指定した操作（クリック/スクロール/入力/スクショ）を順に実行する簡易ツール。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=action_description,
    )
    parser.add_argument(
        "-f",
        "--file",
        help="入力 HTML ファイルへのパス（data URI も可）。未指定の場合は組み込みデモを使用。",
    )
    parser.add_argument(
        "-v",
        "--viewport",
        type=int,
        nargs=2,
        metavar=("WIDTH", "HEIGHT"),
        default=(375, 667),
        help="モバイル表示用の viewport サイズ(px)。例: -v 375 667",
    )
    parser.add_argument(
        "-a",
        "--actions",
        type=json.loads,
        default=default_actions,
        help="順番に実行するアクションの JSON 配列。詳細は下部の説明を参照。",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="組み込みのデモ HTML を使用する（--file が不要）。",
    )
    parser.add_argument(
        "--head",
        action="store_true",
        help="ヘッドレスを解除してブラウザウィンドウを表示する。",
    )
    parser.add_argument(
        "--no-sandbox",
        action="store_true",
        help="sandbox を無効にする。",
    )
    args = parser.parse_args()
    if args.test or not args.file:
        if not args.file and not args.test:
            print("[info] --file が指定されていないため組み込みデモ HTML を使用します。")
        html_path = html_string_to_data_uri(test_html())
    else:
        html_path = Path(args.file)
    actions = args.actions if args.actions else []
    run_actions_on_html(html_path, actions, viewport=args.viewport, is_head=args.head, is_no_sandbox=args.no_sandbox)
