import logging; logging.basicConfig(level=logging.INFO)

import asyncio, os, json, time
from datetime import datetime

from aiohttp import web



#创建一个request handler
def index(request):
    return web.Response(body=b'<h1>Awesome</h1>',headers={'content-type':'text/html'})

async def init(loop): #初始化服务器
    app = web.Application(loop=loop) #创建一个Application实例
    app.router.add_route('GET', '/', index) #用实例对request handler注册
    srv = await loop.create_server(app.make_handler(), '127.0.0.1', 9000) #创建服务器，绑定地址，端口和handler
    logging.info('server started at http://127.0.0.1:9000...')
    return srv

loop = asyncio.get_event_loop() #获取EventLoop
loop.run_until_complete(init(loop)) #执行协程
loop.run_forever() #服务器不关闭

