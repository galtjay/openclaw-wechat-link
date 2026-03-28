import asyncio
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from typing import Dict, List, Optional

app = FastAPI()
templates = Jinja2Templates(directory=".")

# ==========================================
# 配置：全局访问令牌
# ==========================================
GLOBAL_TOKEN = "WE_LOVE_WMW"  # 修改为你自己的复杂 Token

# ==========================================
# 1. 连接与房间管理器
# ==========================================
class ConnectionManager:
    def __init__(self):
        self.qr_codes: Dict[str, str] = {}
        self.logs: Dict[str, List[str]] = {}
        self.viewers: Dict[str, List[WebSocket]] = {}
        self.uploaders: Dict[str, WebSocket] = {}
        self.room_tasks: Dict[str, asyncio.Task] = {}

    async def init_room_if_needed(self, client_id: str):
        if client_id not in self.room_tasks:
            loop = asyncio.get_running_loop()
            self.room_tasks[client_id] = loop.create_task(self._room_timer(client_id))
            print(f"房间 {client_id} 已创建。")

    async def _room_timer(self, client_id: str):
        await asyncio.sleep(300) # 5分钟有效
        await self.destroy_room(client_id, reason="房间有效期已到")

    async def connect_viewer(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        await self.init_room_if_needed(client_id)
        if client_id not in self.viewers: self.viewers[client_id] = []
        self.viewers[client_id].append(websocket)
        
        if client_id in self.qr_codes:
            await websocket.send_text(json.dumps({"type": "qrcode", "data": self.qr_codes[client_id]}))
        if client_id in self.logs:
            for log in self.logs[client_id]:
                await websocket.send_text(json.dumps({"type": "log", "data": log}))

    def disconnect_viewer(self, websocket: WebSocket, client_id: str):
        if client_id in self.viewers and websocket in self.viewers[client_id]:
            self.viewers[client_id].remove(websocket)

    async def connect_uploader(self, websocket: WebSocket, client_id: str):
        await websocket.accept()
        await self.init_room_if_needed(client_id)
        self.uploaders[client_id] = websocket

    async def broadcast_data(self, client_id: str, msg_type: str, content: str):
        if msg_type == "qrcode": self.qr_codes[client_id] = content
        elif msg_type == "log":
            if client_id not in self.logs: self.logs[client_id] = []
            self.logs[client_id].append(content)
            if len(self.logs[client_id]) > 200: self.logs[client_id].pop(0)

        if client_id in self.viewers:
            msg = json.dumps({"type": msg_type, "data": content})
            for v in self.viewers[client_id]:
                try: await v.send_text(msg)
                except: pass

    async def destroy_room(self, client_id: str, reason: str):
        # 清理逻辑同前...
        for ws_list in [self.viewers.get(client_id, []), [self.uploaders.get(client_id)]]:
            for ws in ws_list:
                if ws: 
                    try: await ws.close(code=1000, reason=reason)
                    except: pass
        self.viewers.pop(client_id, None); self.uploaders.pop(client_id, None)
        self.qr_codes.pop(client_id, None); self.logs.pop(client_id, None)
        if client_id in self.room_tasks:
            self.room_tasks[client_id].cancel()
            del self.room_tasks[client_id]

manager = ConnectionManager()

# ==========================================
# 2. 路由 (带 Token 验证)
# ==========================================

@app.get("/openclaw/wechat-link/{client_id}", response_class=HTMLResponse)
async def get_qrcode_page(request: Request, client_id: str, token: Optional[str] = None):
    # 验证 HTTP Token
    if token != GLOBAL_TOKEN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid Token")
    
    return templates.TemplateResponse(
        request=request, name="index.html", context={"client_id": client_id}
    )

@app.websocket("/openclaw/ws/view/{client_id}")
async def websocket_viewer(websocket: WebSocket, client_id: str, token: Optional[str] = None):
    # 验证 WebSocket Token
    if token != GLOBAL_TOKEN:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    
    await manager.connect_viewer(websocket, client_id)
    try:
        while True: await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect_viewer(websocket, client_id)

@app.websocket("/openclaw/ws/upload/{client_id}")
async def websocket_uploader(websocket: WebSocket, client_id: str, token: Optional[str] = None):
    # 验证 WebSocket Token
    if token != GLOBAL_TOKEN:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect_uploader(websocket, client_id)
    try:
        while True:
            raw = await websocket.receive_text()
            # 兼容逻辑...
            if raw.startswith("{"):
                msg = json.loads(raw)
                m_type, content = msg.get("type", "log"), msg.get("data", "")
            else:
                m_type = "qrcode" if (len(raw) > 1000 or raw.startswith("data:image")) else "log"
                content = raw
            await manager.broadcast_data(client_id, m_type, content)
    except WebSocketDisconnect:
        manager.disconnect_uploader(client_id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)