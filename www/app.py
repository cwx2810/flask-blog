import logging; logging.basicConfig(level=logging.INFO)

import asyncio, os, json, time
from datetime import datetime

from aiohttp import web

from jinja2 import Environment, FileSystemLoader

from config import configs

import orm
from coroweb import add_routes, add_static


# 初始化模板文件
def init_jinja2(app, **kw):
    logging.info('init jinja2...')
    # 配置options参数
    options = dict(
        # 自动转义xml/html的特殊字符
        autoescape = kw.get('autoescape', True),
        # 定义代码块的开始、结束标志
        block_start_string = kw.get('block_start_string', '{%'),
        block_end_string = kw.get('block_end_string', '%}'),
        # 定义变量的开始、结束标志
        variable_start_string = kw.get('variable_start_string', '{{'),
        variable_end_string = kw.get('variable_end_string', '}}'),
        # 自动加载修改后的模板文件
        auto_reload = kw.get('auto_reload', True)
    )
    # 获取模板文件夹路径
    path = kw.get('path', None)
    if path is None:
        # 拼接路径目录
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    logging.info('set jinja2 template path: %s' % path)
    # Environment类是jinja2的核心类，用来保存配置、全局对象以及模板文件的路径
    # FileSystemLoader类加载path路径中的模板文件
    env = Environment(loader=FileSystemLoader(path), **options)
    # 过滤器集合
    filters = kw.get('filters', None)
    if filters is not None:
        for name, f in filters.items():
            # filters是Environment类的属性：过滤器字典
            env.filters[name] = f
    # 所有的一切是为了给app添加__templating__字段
    # 前面将jinja2的环境配置都赋值给env了，这里再把env存入app的dict中，这样app就知道要到哪儿去找模板，怎么解析模板。
    app['__templating__'] = env         # app是一个dict-like对象

# 编写用于输出日志的middleware拦截器
# handler是URL处理函数
# 有了此拦截器，URL处理函数处理哪个方法都在控制台一目了然
@asyncio.coroutine
def logger_factory(app, handler):
    @asyncio.coroutine
    def logger(request):
        logging.info('Request: %s %s' % (request.method, request.path))
        # await asyncio.sleep(0.3)
        return (yield from handler(request))
    return logger

@asyncio.coroutine
def data_factory(app, handler):
    @asyncio.coroutine
    def parse_data(request):
        if request.method == 'POST':
            if request.content_type.startswith('application/json'):
                request.__data__ = yield from request.json()
                logging.info('request json: %s' % str(request.__data__))
            elif request.content_type.startswith('application/x-www-form-urlencoded'):
                request.__data__ = yield from request.post()
                logging.info('request form: %s' % str(request.__data__))
        return (yield from handler(request))
    return parse_data

# 这个拦截器处理URL处理函数返回值，在这里request最终被转换成response
@asyncio.coroutine
def response_factory(app, handler):
    @asyncio.coroutine
    def response(request):
        logging.info('Response handler...')
        # r是经过URL处理函数处理后的返回值
        r = yield from handler(request)
        # 如果r直接就是response对象，直接返回
        # StreamResponse是所有Response对象的父类
        if isinstance(r, web.StreamResponse):
            return r
        # 如果r是字节码对象
        if isinstance(r, bytes):
            # 字节码继承自StreamResponse，接受body参数，构造HTTP响应内容
            resp = web.Response(body=r)
            # Response的content_type属性
            resp.content_type = 'application/octet-stream'
            return resp
        # 如果r是string对象
        if isinstance(r, str):
            # 若r以返回重定向字符串开头
            if r.startswith('redirect:'):
                # 重定向至目标URL
                return web.HTTPFound(r[9:])
            # 同上，构造HTTP相应内容
            resp = web.Response(body=r.encode('utf-8'))
            # utf-8编码的text格式
            resp.content_type = 'text/html;charset=utf-8'
            return resp
        # r为dict对象时
        if isinstance(r, dict):
            # 在后续构造URL处理函数返回值时，会加入__template__值，用以选择渲染的模板
            template = r.get('__template__')
            # 不带模板信息，返回json对象
            if template is None:
                resp = web.Response(body=json.dumps(r, ensure_ascii=False, default=lambda o: o.__dict__).encode('utf-8'))
                resp.content_type = 'application/json;charset=utf-8'
                return resp
            # 带模板信息，渲染模板
            else:
                resp = web.Response(body=app['__templating__'].get_template(template).render(**r).encode('utf-8'))
                # utf-8编码的html格式
                resp.content_type = 'text/html;charset=utf-8'
                return resp
        # 返回响应码
        if isinstance(r, int) and r >= 100 and r < 600:
            return web.Response(r)
        # 返回了一组响应代码和原因，如：(200, 'OK'), (404, 'Not Found')
        if isinstance(r, tuple) and len(r) == 2:
            t, m = r
            if isinstance(t, int) and t >= 100 and t < 600:
                return web.Response(t, str(m))
        # 均以上条件不满足，默认返回
        resp = web.Response(body=str(r).encode('utf-8'))
        # utf-8纯文本
        resp.content_type = 'text/plain;charset=utf-8'
        return resp
    return response

# 日期过滤器，数据库中定义的日期不是标准格式，这里要转换一下
def datetime_filter(t):
    delta = int(time.time() - t)
    if delta < 60:
        return u'1分钟前'
    if delta < 3600:
        return u'%s分钟前' % (delta // 60)
    if delta < 86400:
        return u'%s小时前' % (delta // 3600)
    if delta < 604800:
        return u'%s天前' % (delta // 86400)
    dt = datetime.fromtimestamp(t)
    return u'%s年%s月%s日' % (dt.year, dt.month, dt.day)


#初始化服务器
@asyncio.coroutine
def init(loop):
    # 创建数据库连接池
    # await orm.create_pool(loop=loop, host='127.0.0.1', port=3306, user='root', password='admin', db='blog')
    yield from orm.create_pool(loop=loop, **configs.db)
    # 创建一个Application实例，加入拦截器
    app = web.Application(loop=loop, middlewares=[logger_factory, response_factory])
    # 初始化jinjia2模板
    init_jinja2(app, filters=dict(datetime=datetime_filter))
    # 注册url处理函数，在handlers.py中定义映射路径
    add_routes(app, 'handlers')
    # 添加静态文件
    add_static(app)
    # 创建服务器，绑定地址，端口和handler
    srv = yield from loop.create_server(app.make_handler(), '127.0.0.1', 9000)
    logging.info('server started at http://127.0.0.1:9000...')
    return srv

loop = asyncio.get_event_loop() #获取EventLoop
loop.run_until_complete(init(loop)) #用loop执行协程初始化服务器
loop.run_forever() #用loop控制服务器不关闭

