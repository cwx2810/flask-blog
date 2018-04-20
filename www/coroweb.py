import asyncio, os, inspect, logging, functools

from urllib import parse

from aiohttp import web

from apis import APIError

# 定义装饰器，从用户输入的URL获得HTTP请求是get还是post方法
def get(path):
    # Define decorator @get('/path')
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)
        wrapper.__method__ = 'GET'
        wrapper.__route__ = path
        return wrapper
    return decorator

def post(path):
    # Define decorator @post('/path')
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kw):
            return func(*args, **kw)
        wrapper.__method__ = 'POST'
        wrapper.__route__ = path
        return wrapper
    return decorator

# 用inspect方法分析URL处理函数中的参数，之后从request中提取，转换为response
# 获取无默认值的命名关键词参数
def get_required_kw_args(fn):
    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        # 如果视图函数存在命名关键字参数，且默认值为空，获取它的key（参数名）
        if param.kind == inspect.Parameter.KEYWORD_ONLY and param.default == inspect.Parameter.empty:
            args.append(name)
    return tuple(args)

# 获取命名关键词参数
def get_named_kw_args(fn):
    args = []
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            args.append(name)
    return tuple(args)

# 判断是否有命名关键词参数
def has_named_kw_args(fn):
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.KEYWORD_ONLY:
            return True

# 判断是否有关键词参数
def has_var_kw_arg(fn):
    params = inspect.signature(fn).parameters
    for name, param in params.items():
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            return True

# 判断是否含有名叫'request'的参数，且位置在最后
def has_request_arg(fn):
    sig = inspect.signature(fn)
    params = sig.parameters
    found = False
    for name, param in params.items():
        if name == 'request':
            found = True
            continue
        if found and (
            param.kind != inspect.Parameter.VAR_POSITIONAL and
            param.kind != inspect.Parameter.KEYWORD_ONLY and
            param.kind != inspect.Parameter.VAR_KEYWORD):
            # 若判断为True，表明param只能是位置参数。且该参数位于request之后，故不满足条件，报错。
            raise ValueError('request parameter must be the last named parameter in function: %s%s' % (fn.__name__, str(sig)))
    return found


# URL处理函数，从request获取参数，转换为response
class RequestHandler(object):
    #初始化URL处理函数中的参数
    def __init__(self, app, fn):
        self._app = app
        self._func = fn
        self._has_request_arg = has_request_arg(fn)
        self._has_var_kw_arg = has_var_kw_arg(fn)
        self._has_named_kw_args = has_named_kw_args(fn)
        self._named_kw_args = get_named_kw_args(fn)
        self._required_kw_args = get_required_kw_args(fn)

    @asyncio.coroutine
    def __call__(self, request):
        # 定义kw，用于保存request中参数
        kw = None
        # 若URL处理函数有命名关键词或关键词参数
        if self._has_var_kw_arg or self._has_named_kw_args or self._required_kw_args:
            # 如果用户调用的是post方法
            if request.method == 'POST':
                # 根据request参数中的content_type字段，确定不同的解析方法
                if not request.content_type:
                    # 如果content_type不存在，返回400错误
                    return web.HTTPBadRequest('Missing Content-Type.')
                # 将字段转换成小写，便于检查
                ct = request.content_type.lower()
                # 如果contenttype字段以json格式数据开头
                if ct.startswith('application/json'):
                    # 保存json数据，request.json()返回dict对象
                    params = yield from request.json()
                    # 如果不是dict，报错
                    if not isinstance(params, dict):
                        return web.HTTPBadRequest('JSON body must be object.')
                    # 保存request中的参数
                    kw = params
                # 如果contenttype字段以form表单请求的编码形式开头
                elif ct.startswith('application/x-www-form-urlencoded') or ct.startswith('multipart/form-data'):
                    # 保存post数据，dict-like对象。
                    params = yield from request.post()
                    # 组成dict，统一kw格式
                    kw = dict(**params)
                else:
                    # 否则报错，contenttype存在，但是不支持的格式
                    return web.HTTPBadRequest('Unsupported Content-Type: %s' % request.content_type)
            # 如果用户调用的是GET方法
            if request.method == 'GET':
                # 返回URL查询语句，?后的键值。string形式。
                qs = request.query_string
                if qs:
                    kw = dict()
                    # 解析url中?后面的键值对的内容
                    # 返回查询变量和值的映射，dict对象。True表示不忽略空格。
                    for k, v in parse.parse_qs(qs, True).items():
                        kw[k] = v[0]
        # 若以上操作并没有保存到参数，就是kw中没有参数，也就是用户提交的request中没参数
        if kw is None:
            # request.match_info返回dict对象。可变路由中的可变字段{variable}为参数名，传入request请求的path为值
            kw = dict(**request.match_info)
        # request有参数
        else:
            # 若URL处理函数只有命名关键词参数没有关键词参数
            if not self._has_var_kw_arg and self._named_kw_args:
                # 定义一个字典只保留命名关键词参数，不要关键词参数
                copy = dict()
                # 只保留命名关键词参数
                for name in self._named_kw_args:
                    if name in kw:
                        copy[name] = kw[name]
                # kw中只存在命名关键词参数
                kw = copy
            # 将request.match_info中的参数传入kw
            for k, v in request.match_info.items():
                # 检查kw中的参数是否和match_info中的重复
                if k in kw:
                    logging.warning('Duplicate arg name in named arg and kw args: %s' % k)
                kw[k] = v
        # 经过以上操作，kw保存到了用户提交的request参数
        # 假如URL处理函数存在名叫request参数，则将kw中的传入
        if self._has_request_arg:
            kw['request'] = request
        # 如果URL处理函数存在无默认值的命名关键词参数
        if self._required_kw_args:
            for name in self._required_kw_args:
                # 若kw中没有保存到参数值，报错。
                if not name in kw:
                    return web.HTTPBadRequest('Missing argument: %s' % name)
        # 至此，kw为URL处理函数fn真正能调用的参数
        # request请求中的参数，终于全部传递给了URL处理函数
        logging.info('call with args: %s' % str(kw))
        try:
            r = yield from self._func(**kw)
            return r
        except APIError as e:
            return dict(error=e.error, data=e.data, message=e.message)

# 添加静态文件，css、img、js
def add_static(app):
    # 拼接文件目录
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
    # 注册的时候调用静态文件
    app.router.add_static('/static/', path)
    logging.info('add static %s => %s' % ('/static/', path))


# 注册URL处理函数
def add_route(app, fn):
    # 通过装饰器获取是get还是post
    method = getattr(fn, '__method__', None)
    path = getattr(fn, '__route__', None)
    # 如果没有获取到这俩关键的参数就报错
    if path is None or method is None:
        raise ValueError('@get or @post not defined in %s.' % str(fn))
    # 判断URL处理函数是否协程并且是生成器
    if not asyncio.iscoroutinefunction(fn) and not inspect.isgeneratorfunction(fn):
        # 将URL处理函数转变成协程
        fn = asyncio.coroutine(fn)
    logging.info('add route %s %s => %s(%s)' % (method, path, fn.__name__, ', '.join(inspect.signature(fn).parameters.keys())))
    # 在app中注册经RequestHandler类封装的URL处理函数
    # 这样app的路由就和URL处理函数连接起来了，在前台输入相应的path就能进行解析
    app.router.add_route(method, path, RequestHandler(app, fn))

# 批量注册
def add_routes(app, module_name):
    n = module_name.rfind('.')
    # 如果module_name中没有点，直接import之
    if n == (-1):
        mod = __import__(module_name, globals(), locals())
    # 如果带点，比如A.B，要从A中加载B
    else:
        # 获取B
        name = module_name[n+1:]
        # 获取A中的B
        mod = getattr(__import__(module_name[:n], globals(), locals(), [name]), name)
    # 迭代module中所有对象
    for attr in dir(mod):
        # 忽略_开头的对象
        if attr.startswith('_'):
            continue
        # 模块中的对象就是要注册的函数
        fn = getattr(mod, attr)
        # 确保是函数
        if callable(fn):
            # 这两句成立则要注册的函数中没有方法
            method = getattr(fn, '__method__', None)
            path = getattr(fn, '__route__', None)
            # 如果method和path不是none，都存在，就批量注册
            if method and path:
                add_route(app, fn)