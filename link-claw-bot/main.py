import asyncio
import json
import websockets
import random
import sys
import string
import io
import base64
from PIL import Image, ImageDraw

# ==========================================
# 配置区：请确保与 main.py 中的设置一致
# ==========================================
SERVER_HOST = "https://cdn.watermelonwater.tech"
TARGET_ID = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
MY_TOKEN = "WE_LOVE_WMW"
INSTALL_CMD="""\
export NODE_OPTIONS="--max-old-space-size=1024" && \

# 设定 npm 全局镜像源
npm config set registry https://registry.npmmirror.com && \

# 清理 npx 缓存，避免旧文件导致权限或版本问题
rm -rf ~/.npm/_npx/* || true && \

# 卸载可能已存在的旧版 CLI，防止使用到旧缓存
npm uninstall -g @tencent-weixin/openclaw-weixin-cli || true && \

# 升级 npm（可在需要时替换成你本地更快的镜像）
npm install -g npm && \

# 清理可能残留的插件安装临时目录，防止出现 duplicate plugin id
find ~/ -maxdepth 3 -type d -name ".openclaw-install-stage-*" -exec rm -rf {} + || true && \

# 检测 OpenClaw 插件目录是否已存在；如已存在，则跳过安装，直接执行重新配对
if find ~/ -maxdepth 5 -type d -name "openclaw-weixin" | grep -q "openclaw-weixin"; then \
    echo "检测到插件已安装：执行重新配对"; \
    openclaw channels login --channel openclaw-weixin || true; \
    exit 0; \
fi && \

# 插件不存在 → 全新安装
# 使用 npx 安装最新版本 CLI，并让 CLI 自己决定安装位置（更通用）
npx --registry=https://registry.npmmirror.com -y @tencent-weixin/openclaw-weixin-cli@latest install
"""

def ascii_to_base64_qr(ascii_lines):
    module_size = 10
    quiet_zone = 4
    bg_color = (255, 255, 255)
    fg_color = (0, 0, 0)

    # 有时最后一行可能有空格或者其它杂质，做一下简单的过滤，但保持原有的逻辑
    lines = [line.strip('\r\n') for line in ascii_lines if line]
    if not lines:
        return ""
        
    qr_width = len(lines[0])
    qr_height = len(lines) * 2

    total_width = (qr_width + quiet_zone*2) * module_size
    total_height = (qr_height + quiet_zone*2) * module_size

    img = Image.new('RGB', (total_width, total_height), bg_color)
    draw = ImageDraw.Draw(img)

    def get_modules(char):
        if char == ' ': return [0, 0]
        elif char == '█': return [1, 1]
        elif char == '▄': return [0, 1]
        elif char == '▀': return [1, 0]
        else: return [1, 1]  # 兜底或者未知字符默认当黑色快

    for y, line in enumerate(lines):
        for x, char in enumerate(line):
            modules = get_modules(char)
            for row_offset, is_black in enumerate(modules):
                if is_black:
                    left = (x + quiet_zone) * module_size
                    top = (y*2 + row_offset + quiet_zone) * module_size
                    right = left + module_size
                    bottom = top + module_size
                    draw.rectangle([left, top, right-1, bottom-1], fill=fg_color)
                    
    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode('utf-8')
    return f"data:image/png;base64,{img_str}"

async def run_uploader():
    # 动态将 http(s):// 转换为 ws(s)://
    host = SERVER_HOST
    if host.startswith("https://"):
        base_ws = "wss://" + host[len("https://"):]
    elif host.startswith("http://"):
        base_ws = "ws://" + host[len("http://"):]
    else:
        base_ws = "ws://" + host
        
    # 将给 LLM 识别的内容写入到指定的文件中
    with open("/tmp/link.txt", "w", encoding="utf-8") as f:
        f.write("请将以下内容发送给用户：\n")
        f.write("请打开下方链接完成微信配对：\n")
        f.write(f"[LINK] {host}/openclaw/wechat-link/{TARGET_ID}?token={MY_TOKEN}\n")
    
    print(f"[LINK] {host}/openclaw/wechat-link/{TARGET_ID}?token={MY_TOKEN}")
    
    uri = f"{base_ws}/openclaw/ws/upload/{TARGET_ID}?token={MY_TOKEN}"
    # print(f"[*] 准备连接：{uri}")
    
    # 1. 建立 websocket 连接
    try:
        ws = await websockets.connect(uri)
        # print("[+] Websocket连接成功！后端就绪。")
        # print(f"[*] 请访问网页端：{host}/openclaw/wechat-link/{TARGET_ID}?token={MY_TOKEN}")
    except Exception as e:
        # print(f"[-] 连接失败: {e}")
        return

    # 2. 执行安装命令并捕获流
    # print(f"[*] 正在执行命令: {INSTALL_CMD}")
    process = await asyncio.create_subprocess_shell(
        INSTALL_CMD,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT
    )
    
    ascii_qr_buffer = []

    try:
        while True:
            line_bytes = await process.stdout.readline()
            if not line_bytes:
                # 结束前如果 buffer 还有数据，也要渲染
                if ascii_qr_buffer:
                    b64_qr = ascii_to_base64_qr(ascii_qr_buffer)
                    await ws.send(json.dumps({"type": "qrcode", "data": b64_qr}))
                    ascii_qr_buffer = []
                break
            
            # 解码
            line = line_bytes.decode('utf-8', errors='replace').rstrip('\r\n')
            
            # 在本地控制台照常输出原始日志
            # print(line)
            
            # 检测这行是不是由二维码方块组成的（微信终端扫码标志性字符）
            if any(c in line for c in ['█', '▄', '▀']):
                ascii_qr_buffer.append(line)
                # 这行不经过 websocket 发送到 web 的 terminal 里，以防乱码
                continue
            else:
                # 如果当前行不存在二维码字符，但之前缓存了二维码块数据，说明一个二维码输出完毕了
                if ascii_qr_buffer:
                    # print(f"[!] 识别到一段完整 ASCII 二维码，正在渲染为图片 Base64...")
                    b64_qr = ascii_to_base64_qr(ascii_qr_buffer)
                    await ws.send(json.dumps({
                        "type": "qrcode",
                        "data": b64_qr
                    }))
                    # 清空缓存池等待下一个二维码
                    ascii_qr_buffer = []

            # 正常的其他日志（包含您说的：请用浏览器打开以下链接这种文字提示或者 URL）
            # 我们全部照常下发（Web 的 Terminal 也会接收到文字内容）
            await ws.send(json.dumps({
                "type": "log",
                "data": line
            }))
            
        await process.wait()
        # print(f"[*] 命令执行完毕，退出码: {process.returncode}")
        await ws.send(json.dumps({"type": "log", "data": f"✅ 执行完毕，退出码 {process.returncode}"}))
        
        # print("[!] 保持连接 60 秒后退出...")
        await asyncio.sleep(60)
        
    except websockets.exceptions.ConnectionClosedError as e:
        pass # print(f"[-] Websocket已断开: {e}")
    except Exception as e:
        pass # print(f"[-] 运行报错: {e}")
    finally:
        await ws.close()

if __name__ == "__main__":
    print("正在安装微信插件...")    
    try:
        asyncio.run(run_uploader())
    except KeyboardInterrupt:
        # print("\n[!] 用户中断。")
        sys.exit(0)


