# dummy_gen_image_server.py
import os
import ssl
import urllib.parse
import json
import re
import base64
import time
import random
import argparse
import requests
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import pathlib
import shutil

# === 設定 (Configuration) ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
CERT_DIR = os.path.join(BASE_DIR, "cert")
CERT_PATH = os.path.join(CERT_DIR, "server.pem")
PRESET_DIR = os.path.join(BASE_DIR, "presets")
TRASH_DIR = os.path.join(PUBLIC_DIR, "trash")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
ADMIN_DIR = os.path.join(BASE_DIR, "admin")

# セキュリティ: 許可するIP (ローカルLAN)
# 必要に応じて追加・変更してください
ALLOWED_IPS = ['127.0.0.1', '192.168.1.4545', '192.168.1.454545']

# ディレクトリが存在するか確認し、なければ作成
os.makedirs(PUBLIC_DIR, exist_ok=True)
os.makedirs(PRESET_DIR, exist_ok=True)
os.makedirs(CERT_DIR, exist_ok=True)
os.makedirs(TRASH_DIR, exist_ok=True)
os.makedirs(ADMIN_DIR, exist_ok=True)


# === 設定ファイル管理 ===
def load_config():
    """config.json から設定を読み込みます。なければデフォルト値を返します。"""
    defaults = {
        "backend": "sdwebui",
        "sdwebui_url": "http://127.0.0.1:7860",
        "comfyui_url": "http://127.0.0.1:8188",
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # デフォルトにマージ (保存されてないキーはデフォルト値で補完)
            defaults.update(saved)
        except Exception as e:
            print(f"[⚠️] config.json 読み込みエラー: {e}")
    return defaults

def save_config(config):
    """設定を config.json に保存します。"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"[💾] 設定保存: {CONFIG_PATH}")
    except Exception as e:
        print(f"[🔥] 設定保存エラー: {e}")

def list_preset_folders():
    """presets/ 配下のフォルダ一覧を返します。各フォルダ内のJSONファイル一覧付き。"""
    folders = []
    if not os.path.isdir(PRESET_DIR):
        return folders
    for entry in sorted(os.scandir(PRESET_DIR), key=lambda e: e.name):
        if entry.is_dir():
            files = []
            for f in sorted(os.scandir(entry.path), key=lambda e: e.name):
                if f.is_file() and f.name.endswith(".json"):
                    files.append(f.name[:-5])  # .json を除去
            folders.append({"name": entry.name, "presets": files})
    return folders

# SimpleHTTPRequestHandlerのために作業ディレクトリをPUBLIC_DIRに変更 (安全なデフォルト)
os.chdir(PUBLIC_DIR)


# === コマンドライン引数 ===
def parse_args():
    parser = argparse.ArgumentParser(description="SDinChat 画像生成サーバー")
    parser.add_argument("--backend", choices=["sdwebui", "comfyui"], default="sdwebui",
                        help="画像生成バックエンド (デフォルト: sdwebui)")
    parser.add_argument("--sdwebui-url", default="http://127.0.0.1:7860",
                        help="SD WebUI API の URL (デフォルト: http://127.0.0.1:7860)")
    parser.add_argument("--comfyui-url", default="http://127.0.0.1:8188",
                        help="ComfyUI API の URL (デフォルト: http://127.0.0.1:8188)")
    return parser.parse_args()


def is_safe_path(base_dir, target_path):
    """
    ディレクトリトラバーサルを防ぐため、target_path が base_dir の中にあるかを確認します。
    """
    try:
        # 絶対パス解決
        base = pathlib.Path(base_dir).resolve()
        target = pathlib.Path(target_path).resolve()
        # target が base の内部にあるかチェック
        return target.is_relative_to(base)
    except Exception as e:
        print(f"[⚠️] パスセキュリティチェック失敗: {e}")
        return False

def escape_plain_paren(text):
    """
    強調構文として使われていない括弧をエスケープします (例: "(word:1.1)" は保持し、"(word)" は "\(word\)" にします)。
    """
    pattern = re.compile(r'\(([^()]+?)\)')
    i = 0
    while True:
        def replacer(m):
            inner = m.group(1)
            # 強調構文の判定：末尾が :数字 or :数字.数字
            if re.search(r':-?[0-9]+(?:\.[0-9]+)?$', inner):
                return m.group(0)
            # 直前バックスラッシュ1個以上はスキップ
            if m.start() > 0 and text[m.start() - 1] == '\\':
                return m.group(0)
            # それ以外はエスケープ
            return f'\\({inner}\\)'
        new_text = pattern.sub(replacer, text)
        if new_text == text or i > 10:
            break
        text = new_text
        i += 1
    return text


# =====================================================
#  バックエンド: SD WebUI API
# =====================================================
def generate_with_sdwebui(payload, filepath, sd_api_url):
    """
    SD WebUI API (Automatic1111) を使って画像を生成します。
    成功時は画像バイナリデータを返し、失敗時は例外を投げます。
    """
    print(f"     -> SD WebUIへ送信中... ({sd_api_url})")
    resp = requests.post(url=f"{sd_api_url}/sdapi/v1/txt2img", json=payload, timeout=120)

    if resp.status_code == 200:
        r_json = resp.json()
        images = r_json.get("images", [])
        if images:
            img_data = base64.b64decode(images[0])
            with open(filepath, "wb") as f:
                f.write(img_data)
            print(f"[✅] 保存完了: {filepath}")
            return img_data
        else:
            raise RuntimeError("SD WebUIから画像が返されませんでした")
    else:
        raise RuntimeError(f"SD WebUI エラー: {resp.status_code}")


# =====================================================
#  バックエンド: ComfyUI API
# =====================================================
def _find_nodes_by_class(workflow, class_type):
    """ワークフロー内から指定class_typeのノードを検索"""
    results = []
    for node_id, node in workflow.items():
        if node.get("class_type") == class_type:
            results.append((node_id, node))
    return results

def _inject_comfyui_params(workflow, prompt, negative_prompt, resolution, seed):
    """
    ComfyUIワークフローJSONにパラメータを動的注入します。
    ノードの class_type と _meta.title で自動判定します。
    """
    # ポジティブプロンプトの注入 (CLIPTextEncode で title に "Positive" を含むもの、またはなければ最初のもの)
    clip_nodes = _find_nodes_by_class(workflow, "CLIPTextEncode")
    positive_node = None
    negative_node = None
    for node_id, node in clip_nodes:
        title = node.get("_meta", {}).get("title", "").lower()
        if "positive" in title:
            positive_node = node
        elif "negative" in title:
            negative_node = node

    # titleで判別できなかった場合: 最初の2つをpositive/negativeとする
    if positive_node is None and len(clip_nodes) >= 1:
        positive_node = clip_nodes[0][1]
    if negative_node is None and len(clip_nodes) >= 2:
        negative_node = clip_nodes[1][1]

    if positive_node and prompt:
        base_text = positive_node["inputs"].get("text", "").strip()
        if "{prompt}" in base_text:
            # プレースホルダーがあればその位置に挿入
            positive_node["inputs"]["text"] = base_text.replace("{prompt}", prompt)
        elif base_text:
            # プレースホルダーなし → 先頭に結合
            positive_node["inputs"]["text"] = f"{prompt}, {base_text}"
        else:
            positive_node["inputs"]["text"] = prompt
        print(f"[⚙️] ポジティブプロンプト注入完了")

    if negative_node and negative_prompt:
        base_neg = negative_node["inputs"].get("text", "").strip()
        if "{negative}" in base_neg:
            negative_node["inputs"]["text"] = base_neg.replace("{negative}", negative_prompt)
        elif base_neg:
            negative_node["inputs"]["text"] = f"{negative_prompt}, {base_neg}"
        else:
            negative_node["inputs"]["text"] = negative_prompt
        print(f"[⚙️] ネガティブプロンプト注入完了")

    # 解像度の注入 (EmptyLatentImage)
    if resolution:
        latent_nodes = _find_nodes_by_class(workflow, "EmptyLatentImage")
        for node_id, node in latent_nodes:
            node["inputs"]["width"] = resolution[0]
            node["inputs"]["height"] = resolution[1]
            print(f"[⚙️] 解像度注入: {resolution[0]}x{resolution[1]}")
            break

    # シードの注入 (KSampler / KSamplerAdvanced)
    if seed is not None:
        for sampler_class in ["KSampler", "KSamplerAdvanced"]:
            sampler_nodes = _find_nodes_by_class(workflow, sampler_class)
            for node_id, node in sampler_nodes:
                if "seed" in node.get("inputs", {}):
                    node["inputs"]["seed"] = seed
                    print(f"[⚙️] シード注入: {seed}")
                    break

    return workflow

def generate_with_comfyui(workflow, prompt, negative_prompt, filepath, resolution, comfyui_url):
    """
    ComfyUI API を使って画像を生成します。
    成功時は画像バイナリデータを返し、失敗時は例外を投げます。
    """
    # ランダムシードの生成
    seed = random.randint(0, 2**53 - 1)

    # ワークフローにパラメータを注入
    workflow = _inject_comfyui_params(workflow, prompt, negative_prompt, resolution, seed)

    # 1. POST /prompt でワークフローをキューに送信
    print(f"     -> ComfyUIへ送信中... ({comfyui_url})")
    prompt_payload = {"prompt": workflow}
    resp = requests.post(f"{comfyui_url}/prompt", json=prompt_payload, timeout=30)

    if resp.status_code != 200:
        raise RuntimeError(f"ComfyUI キュー送信エラー: {resp.status_code} - {resp.text}")

    prompt_id = resp.json().get("prompt_id")
    if not prompt_id:
        raise RuntimeError("ComfyUIからprompt_idが返されませんでした")

    print(f"[📋] キュー登録完了: prompt_id={prompt_id}")

    # 2. /history/{prompt_id} をポーリングして完了を待機
    max_wait = 300  # 最大待機秒数
    poll_interval = 1.0  # ポーリング間隔
    elapsed = 0
    history_data = None

    while elapsed < max_wait:
        time.sleep(poll_interval)
        elapsed += poll_interval

        try:
            hist_resp = requests.get(f"{comfyui_url}/history/{prompt_id}", timeout=10)
            if hist_resp.status_code == 200:
                hist_json = hist_resp.json()
                if prompt_id in hist_json:
                    history_data = hist_json[prompt_id]
                    break
        except requests.exceptions.RequestException:
            pass  # 一時的な接続エラーは無視してリトライ

        if elapsed % 10 == 0:
            print(f"     ⏳ 生成待機中... ({int(elapsed)}秒経過)")

    if history_data is None:
        raise RuntimeError(f"ComfyUI 生成タイムアウト ({max_wait}秒)")

    # ステータスチェック
    status = history_data.get("status", {})
    if status.get("status_str") != "success":
        raise RuntimeError(f"ComfyUI 生成失敗: {status}")

    # 3. 出力画像の情報を取得
    outputs = history_data.get("outputs", {})
    image_info = None
    for node_id, node_output in outputs.items():
        images = node_output.get("images", [])
        if images:
            image_info = images[0]
            break

    if image_info is None:
        raise RuntimeError("ComfyUIから出力画像が見つかりませんでした")

    # 4. /view で画像バイナリを取得
    view_params = {
        "filename": image_info["filename"],
        "subfolder": image_info.get("subfolder", ""),
        "type": image_info.get("type", "output"),
    }
    view_resp = requests.get(f"{comfyui_url}/view", params=view_params, timeout=30)

    if view_resp.status_code != 200:
        raise RuntimeError(f"ComfyUI 画像取得エラー: {view_resp.status_code}")

    img_data = view_resp.content

    # ファイルに保存
    with open(filepath, "wb") as f:
        f.write(img_data)
    print(f"[✅] 保存完了: {filepath}")

    return img_data


# =====================================================
#  プリセット検索・バックエンド自動判別
# =====================================================
def _detect_backend(payload):
    """
    プリセットJSONの中身からバックエンドを自動判別します。
    ComfyUIワークフロー: ノードIDをキーとし、各ノードに class_type がある
    SD WebUI: トップレベルに prompt, width, steps 等がある
    """
    if not payload:
        return APP_CONFIG.get("backend", "sdwebui")

    # ComfyUI判定: 値が辞書でclass_typeキーを持つノードが1つでもあれば
    for key, value in payload.items():
        if isinstance(value, dict) and "class_type" in value:
            return "comfyui"

    return "sdwebui"

def _load_preset(folder, filename="default"):
    """
    presets/{folder}/{filename}.json を読み込みます。
    folder: バックエンドフォルダ名 (APP_CONFIG['backend'] から取得)
    filename: プリセットファイル名 (?preset=xxx から取得、デフォルト: default)
    見つからなければ None を返します。
    """
    # セキュリティ: パストラバーサル防止
    for name in (folder, filename):
        if os.sep in name or "/" in name or ".." in name:
            print(f"[⚠️] 不正なプリセット指定: {name}")
            return None

    preset_json = filename if filename.endswith(".json") else f"{filename}.json"
    preset_path = os.path.join(PRESET_DIR, folder, preset_json)

    if not is_safe_path(PRESET_DIR, preset_path):
        print(f"[⚠️] 不正なプリセットパス: {folder}/{preset_json}")
        return None

    if os.path.isfile(preset_path):
        return preset_path

    return None


# =====================================================
#  HTTPリクエストハンドラ
# =====================================================
class GenImageHandler(SimpleHTTPRequestHandler):
    def address_string(self):
        # DNSルックアップを避けてIPをそのまま返すようにオーバーライド
        return self.client_address[0]

    def send_cors_headers(self):
        """CORSヘッダを送信 (UserScriptからのアクセスを許可)"""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Access-Control-Allow-Private-Network")
        self.send_header("Access-Control-Allow-Private-Network", "true") # これが重要: 公開サイトからローカルIPへのアクセス許可

    def end_headers(self):
        """全てのレスポンスにCORSヘッダを付与するためにオーバーライド"""
        self.send_cors_headers()
        super().end_headers()

    def do_OPTIONS(self):
        """プリフライトリクエスト対応"""
        self.send_response(200)
        self.end_headers() # ここでsuper().end_headers()が呼ばれる


    def do_GET(self):
        client_ip = self.client_address[0]
        # 基本的なIPフィルタリング
        if client_ip not in ALLOWED_IPS:
            print(f"[⛔] 接続ブロック: {client_ip}")
            # エラーを返さずに接続を切る（あるいは403を返す）どちらでもよいが、
            # 安全のため403を返しておく
            try:
                self.send_error(403, "Forbidden")
            except:
                pass
            return

        parsed = urllib.parse.urlparse(self.path)
        # URLパスをデコード (スペースなどを処理) し、先頭のスラッシュを削除
        safe_req_path = urllib.parse.unquote(parsed.path).lstrip("/")
        

        # === 管理API: 設定取得 (/api/config) ===
        if parsed.path == "/api/config":
            self._send_json(APP_CONFIG)
            return

        # === 管理API: プリセットフォルダ一覧 (/api/presets) ===
        if parsed.path == "/api/presets":
            self._send_json(list_preset_folders())
            return

        # === 管理画面 (/admin/) ===
        if parsed.path.startswith("/admin"):
            # /admin → /admin/ にリダイレクト
            if parsed.path == "/admin":
                self.send_response(301)
                self.send_header("Location", "/admin/")
                self.end_headers()
                return
            # /admin/ → admin/index.html を返す
            req_file = parsed.path[len("/admin/"):] or "index.html"
            admin_file = os.path.join(ADMIN_DIR, req_file)
            if is_safe_path(ADMIN_DIR, admin_file) and os.path.isfile(admin_file):
                self.send_response(200)
                # Content-Type判定
                if admin_file.endswith(".html"):
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                elif admin_file.endswith(".css"):
                    self.send_header("Content-Type", "text/css; charset=utf-8")
                elif admin_file.endswith(".js"):
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                with open(admin_file, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "Not Found")
            return

        # === 画像ゴミ箱移動 API (/api/trash?path=generate/xyz.webp) ===
        if parsed.path == "/api/trash":
            target_rel_path = urllib.parse.parse_qs(parsed.query).get("path", [""])[0]
            if not target_rel_path:
                self.send_error(400, "Missing path param")
                return

            # 安全性チェック: pathは必ずPUBLIC_DIR以下でなければならない
            full_target_path = os.path.join(PUBLIC_DIR, target_rel_path)
            if not is_safe_path(PUBLIC_DIR, full_target_path):
                print(f"[⛔] 不正な削除リクエストをブロック: {target_rel_path}")
                self.send_error(403, "Forbidden Path")
                return
            
            if not os.path.exists(full_target_path):
                self.send_error(404, "File Not Found")
                return

            try:
                os.makedirs(TRASH_DIR, exist_ok=True)
                filename = os.path.basename(full_target_path)
                # ゴミ箱内でファイル名が重複しないようにする (timestamp付与)
                ts = int(time.time())
                dest_path = os.path.join(TRASH_DIR, f"{ts}_{filename}")
                
                shutil.move(full_target_path, dest_path)
                print(f"[🗑️] ゴミ箱へ移動: {filename} -> {dest_path}")
                
                # 成功時は透明PNGを返す
                png_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
                self.end_headers()
                self.wfile.write(png_data)
            except Exception as e:
                print(f"[🔥] 削除エラー: {e}")
                self.send_error(500, str(e))
            return

        # 絶対パスを構築
        filepath = os.path.join(PUBLIC_DIR, safe_req_path)

        # セキュリティチェック: ディレクトリトラバーサル防止
        if not is_safe_path(PUBLIC_DIR, filepath):
            print(f"[⛔] ディレクトリトラバーサル攻撃をブロック: {self.path}")
            self.send_error(403, "Forbidden Path")
            return
            
        # ファイルが存在すれば SimpleHTTPRequestHandler に任せる
        if os.path.exists(filepath) and os.path.isfile(filepath):
            # self.path を作業ディレクトリ(PUBLIC_DIR)からの相対パスに書き換える
            # SimpleHTTPRequestHandler は self.path を現在のディレクトリ(os.getcwd())からの相対パスとして扱う
            self.path = "/" + safe_req_path
            return super().do_GET()

        # gen_imageフラグがない、かつファイルもないなら404
        qs = urllib.parse.parse_qs(parsed.query)
        if "gen_image" not in qs:
            self.send_error(404, "File Not Found")
            return

        # -------------------------------------------------
        # ここから画像生成ロジック
        # -------------------------------------------------
        prompt = qs.get("prompt", [""])[0]
        negative_prompt = qs.get("negative", [""])[0]
        preset_name = qs.get("preset", ["default"])[0]

        if not prompt:
            self.send_error(400, "No Prompt")
            return

        # バックエンドフォルダは --backend で設定された値（将来WebUIで切替可能）
        backend_folder = APP_CONFIG.get("backend", "sdwebui")

        print(f"[🎨] 生成リクエスト: {safe_req_path}")
        print(f"     Prompt: {prompt[:50]}...")
        if negative_prompt:
            print(f"     Negative: {negative_prompt[:50]}...")
        print(f"     Preset: {backend_folder}/{preset_name}")

        # ディレクトリ作成
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
        except OSError as e:
            print(f"[❌] ディレクトリ作成失敗: {e}")
            self.send_error(500, "Server Error")
            return

        try:
            # --- プリセット読み込み ---
            # presets/{backend_folder}/{preset_name}.json
            preset_path = _load_preset(backend_folder, preset_name)

            payload = {}
            if preset_path:
                with open(preset_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                print(f"[📂] プリセット読み込み: {preset_path}")
            else:
                print(f"[⚠️] プリセットが見つかりません: presets/{backend_folder}/{preset_name}.json")

            # --- バックエンド自動判別 ---
            backend = _detect_backend(payload)
            print(f"     Backend: {backend}")

            # --- 解像度指定 (resolutionクエリ) の解析 ---
            res_param = qs.get("resolution", [None])[0]
            resolution = None
            if res_param:
                # res_W_H, W_H, WxH いずれの形式も許容
                res_match = re.search(r'(?:res_)?(\d+)[_x](\d+)', res_param)
                if res_match:
                    try:
                        w = int(res_match.group(1))
                        h = int(res_match.group(2))
                        resolution = (w, h)
                        print(f"[⚙️] 解像度指定(Query): {w}x{h}")
                    except ValueError:
                        print(f"[⚠️] 解像度指定のパース失敗: {res_param}")

            # --- バックエンド別の生成処理 ---
            if backend == "comfyui":
                img_data = self._generate_comfyui(payload, prompt, negative_prompt, filepath, resolution)
            else:
                img_data = self._generate_sdwebui(payload, prompt, negative_prompt, filepath, resolution)

            # 成功レスポンス
            self.send_response(200)
            self.send_header("Content-Type", "image/webp")
            self.end_headers()
            self.wfile.write(img_data)

        except Exception as e:
            print(f"[🔥] 生成中に例外発生: {e}")
            self.send_error(500, f"Internal Error: {e}")

    def _generate_sdwebui(self, payload, prompt, negative_prompt, filepath, resolution):
        """SD WebUI バックエンドでの画像生成"""
        # 解像度の適用
        if resolution:
            payload["width"] = resolution[0]
            payload["height"] = resolution[1]

        # --- プロンプト処理 ---
        # 1. ユーザープロンプトのエスケープ処理
        user_prompt = escape_plain_paren(prompt)
        
        # 2. [xxx] を __xxx__ に変換（チャットUIでのマークダウン回避策）
        # これによりユーザーは[baseprompt]と書け、SDには__baseprompt__として渡される
        user_prompt = re.sub(r'\[([^\[\]]+)\]', r'__\1__', user_prompt).strip()
        
        # 3. ユーザープロンプトとプリセットプロンプトを結合
        # __baseprompt__ などの展開はSD側のdynamic prompts機能が処理する
        base_prompt = payload.get("prompt", "").strip()
        
        if base_prompt:
            final_prompt = f"{user_prompt}, {base_prompt}"
        else:
            final_prompt = user_prompt
        
        payload["prompt"] = final_prompt

        # 4. ネガティブプロンプトの処理
        if negative_prompt:
            user_neg = escape_plain_paren(negative_prompt)
            user_neg = re.sub(r'\[([^\[\]]+)\]', r'__\1__', user_neg).strip()
            base_neg = payload.get("negative_prompt", "").strip()
            if base_neg:
                payload["negative_prompt"] = f"{user_neg}, {base_neg}"
            else:
                payload["negative_prompt"] = user_neg

        print(f"[✅] 最終プロンプト: {payload['prompt'][:80]}...")
        if payload.get("negative_prompt"):
            print(f"[✅] 最終ネガティブ: {payload['negative_prompt'][:80]}...")

        return generate_with_sdwebui(payload, filepath, APP_CONFIG["sdwebui_url"])

    def _generate_comfyui(self, workflow, prompt, negative_prompt, filepath, resolution):
        """ComfyUI バックエンドでの画像生成"""
        if not workflow:
            raise RuntimeError("ComfyUIにはワークフローJSONのプリセットが必要です")

        # --- プロンプト処理 ---
        user_prompt = escape_plain_paren(prompt)
        user_prompt = re.sub(r'\[([^\[\]]+)\]', r'__\1__', user_prompt).strip()
        
        user_neg = ""
        if negative_prompt:
            user_neg = escape_plain_paren(negative_prompt)
            user_neg = re.sub(r'\[([^\[\]]+)\]', r'__\1__', user_neg).strip()

        print(f"[✅] 最終プロンプト: {user_prompt[:80]}...")
        if user_neg:
            print(f"[✅] 最終ネガティブ: {user_neg[:80]}...")

        return generate_with_comfyui(
            workflow, user_prompt, user_neg, filepath, resolution,
            APP_CONFIG["comfyui_url"]
        )


    def _send_json(self, data, status=200):
        """JSONレスポンスを送信するヘルパー"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        """POSTリクエスト: 設定変更API"""
        client_ip = self.client_address[0]
        if client_ip not in ALLOWED_IPS:
            self.send_error(403, "Forbidden")
            return

        parsed = urllib.parse.urlparse(self.path)

        # === 設定変更 (/api/config) ===
        if parsed.path == "/api/config":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                new_config = json.loads(body)

                # 許可するキーのみ更新
                allowed_keys = ["backend", "sdwebui_url", "comfyui_url"]
                for key in allowed_keys:
                    if key in new_config:
                        APP_CONFIG[key] = new_config[key]

                # ファイルに永続化
                save_config({k: APP_CONFIG[k] for k in allowed_keys if k in APP_CONFIG})

                print(f"[⚙️] 設定変更: {APP_CONFIG}")
                self._send_json({"status": "ok", "config": APP_CONFIG})

            except Exception as e:
                print(f"[🔥] 設定変更エラー: {e}")
                self._send_json({"status": "error", "message": str(e)}, status=400)
            return

        self.send_error(404, "Not Found")


# =====================================================
#  メイン
# =====================================================
# グローバル設定 (argparseの結果を保持)
APP_CONFIG = {}

if __name__ == "__main__":
    args = parse_args()

    # 1. config.json から設定を読み込み
    APP_CONFIG.update(load_config())

    # 2. コマンドライン引数で明示的に指定された場合は上書き
    #    (argparseのデフォルト値と異なる場合のみ上書き)
    if args.backend != "sdwebui" or "backend" not in APP_CONFIG:
        APP_CONFIG["backend"] = args.backend
    if args.sdwebui_url != "http://127.0.0.1:7860":
        APP_CONFIG["sdwebui_url"] = args.sdwebui_url
    if args.comfyui_url != "http://127.0.0.1:8188":
        APP_CONFIG["comfyui_url"] = args.comfyui_url

    print(f"[🔧] バックエンド: {APP_CONFIG['backend']}")
    print(f"[🔧] SD WebUI URL: {APP_CONFIG['sdwebui_url']}")
    print(f"[🔧] ComfyUI URL: {APP_CONFIG['comfyui_url']}")
    print(f"[🔧] 管理画面: https://127.0.0.1:8443/admin/")

    server_address = ('0.0.0.0', 8443)
    httpd = ThreadingHTTPServer(server_address, GenImageHandler)

    # SSL対応
    if os.path.exists(CERT_PATH):
        print(f"[🔒] SSL証明書を読み込みます: {CERT_PATH}")
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_ctx.load_cert_chain(certfile=CERT_PATH)
        httpd.socket = ssl_ctx.wrap_socket(httpd.socket, server_side=True)
    else:
        print(f"[⚠️] 証明書が見つかりません ({CERT_PATH})。HTTP(非SSL)で起動します。")

    print(f"Starting server on port {server_address[1]}...")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server.")
        httpd.shutdown()
