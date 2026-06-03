
import os
import logging
import asyncio
from typing import Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from pyngrok import ngrok, conf

logger = logging.getLogger("CanvasWebServer")

class CanvasWebServer:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(CanvasWebServer, cls).__new__(cls)
        return cls._instance

    def __init__(self, port: int = 8080):
        if hasattr(self, "_initialized") and self._initialized:
            return
            
        self.port = port
        self.app = FastAPI(title="Canvas Workspace Server")
        self.active_sockets: Set[WebSocket] = set()
        self._initialized = True
        
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        web_dir = os.path.join(os.getcwd(), "web")
        if not os.path.exists(web_dir):
            os.makedirs(web_dir)
            logger.warning(f"Web static asset folder not found. Created empty directory: {web_dir}")

        self.app.mount("/static", StaticFiles(directory=web_dir), name="static")

        self.app.add_api_route("/", self.handle_home, methods=["GET"])
        self.app.add_api_route("/ping", self.handle_ping, methods=["GET"])
        self.app.add_api_websocket_route("/ws", self.websocket_endpoint)

    @classmethod
    def get_server(cls):
        return cls._instance

    async def handle_home(self):
        index_path = os.path.join(os.getcwd(), "web", "index.html")
        if os.path.exists(index_path):
            from fastapi.responses import HTMLResponse
            with open(index_path, "r", encoding="utf-8") as f:
                return HTMLResponse(content=f.read(), status_code=200)
        return {"error": "Canvas Web Interface is booting up. Please wait."}

    async def handle_ping(self):
        return {"status": "alive", "message": "Canvas API is running!"}

    async def websocket_endpoint(self, websocket: WebSocket):
        await websocket.accept()
        self.active_sockets.add(websocket)
        logger.info(f"New Canvas Client connected from {websocket.client}")
        try:
            while True:
                data = await websocket.receive_text()
                logger.info(f"Received Canvas edit update from client: {data}")
                
                for socket in self.active_sockets:
                    if socket != websocket:
                        try:
                            await socket.send_text(data)
                        except Exception as e:
                            logger.warning(f"Failed to echo change to {socket}: {e}")
                        
        except WebSocketDisconnect:
            self.active_sockets.remove(websocket)
            logger.info("Canvas client disconnected.")
        except Exception as err:
            logger.error(f"WebSocket processing error: {err}")
            self.active_sockets.discard(websocket)

    async def broadcast_code_update(self, code_text: str):
        if not self.active_sockets:
            logger.info("No active Canvas clients connected. Skipping broadcast.")
            return
            
        logger.info(f"Broadcasting code update to {len(self.active_sockets)} clients...")
        tasks = []
        for socket in list(self.active_sockets):
            tasks.append(socket.send_text(code_text))
            
        await asyncio.gather(*tasks, return_exceptions=True)

    def start_ngrok_tunnel(self) -> str:
        token = os.getenv("NGROK_AUTHTOKEN")
        if not token:
            logger.error(
                "CRITICAL: 'NGROK_AUTHTOKEN' is missing from your .env file! "
                "Programmatic tunneling failed. The Canvas will not load in Discord."
            )
            return ""

        try:
            conf.get_default().auth_token = token
            
            tunnel_url = ngrok.connect(
                self.port, 
                bind_tls=True, 
                hostname="sprinkler-sincerity-nest.ngrok-free.dev"
            ).public_url
            
            logger.info("=" * 60)
            logger.info(f"✅ Secure ngrok tunnel successfully bound!")
            logger.info(f"🔗 Public URL: {tunnel_url}")
            logger.info(f"🛠️  Map this URL in Discord Portal: {tunnel_url}")
            logger.info("=" * 60)
            return tunnel_url
        except Exception as e:
            logger.error(f"Failed to start ngrok tunnel programmatically: {e}")
            return ""

    async def start_server_task(self):
        config = uvicorn.Config(
            app=self.app, 
            host="0.0.0.0", 
            port=self.port, 
            log_level="info", 
            loop="asyncio"
        )
        server = uvicorn.Server(config)
        
        server.install_signal_handlers = lambda: None
        
        logger.info(f"Starting Canvas FastAPI Server locally on port {self.port}...")
        
        self.start_ngrok_tunnel()
        
        await server.serve()