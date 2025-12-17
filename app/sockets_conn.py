
from typing import Dict, List
from fastapi import WebSocket

class CustomerManager:
    def __init__(self):
        self.active: Dict[int, WebSocket] = {} 
    async def connect(self, customer_id: int, websocket: WebSocket):
        await websocket.accept()
        prev = self.active.get(customer_id)
        if prev:
            try:
                await prev.close()
            except:
                pass
        self.active[customer_id] = websocket

    def disconnect(self, customer_id: int):
        if customer_id in self.active:
            del self.active[customer_id]

    async def send_to(self, customer_id: int, message: dict):
        ws = self.active.get(customer_id)
        if ws:
            await ws.send_json(message)

customer_manager = CustomerManager()

class OwnerManager:
    def __init__(self):
        self.active: Dict[int, List[WebSocket]] = {} 

    async def broadcast(self, message: dict):
        to_remove = []
        for owner_id, lst in self.active.items():
            for ws in lst:
                try:
                    await ws.send_json(message)
                except RuntimeError:
                    to_remove.append((owner_id, ws))

        for owner_id, ws in to_remove:
            lst = self.active.get(owner_id)
            if lst and ws in lst:
                lst.remove(ws)
            if lst and len(lst) == 0:
                self.active.pop(owner_id, None)

    async def connect(self, owner_id: int, websocket: WebSocket):
        await websocket.accept()
        lst = self.active.setdefault(owner_id, [])
        lst.append(websocket)
       

    def disconnect(self, owner_id: int, websocket: WebSocket):
        lst = self.active.get(owner_id)
        if not lst:
            return
        try:
            lst.remove(websocket)
        except ValueError:
            pass
        if not lst:
            self.active.pop(owner_id, None)

    async def send_to_owner(self, owner_id: int, message: dict):
        for ws in self.active.get(owner_id, []):
            await ws.send_json(message)

owner_manager = OwnerManager()
