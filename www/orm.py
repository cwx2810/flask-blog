import asyncio, logging

import aiomysql

logging.basicConfig(level=logging.INFO)
def log(sql, args=()):
    logging.info('SQL: %s' % sql)

# 创建sql连接池
async def create_pool(loop, **kw):
    logging.info('create database connection pool...')
    global __pool
    __pool = await aiomysql.create_pool(
        host=kw.get('host', 'localhost'),
        port=kw.get('port', 3306),
        user=kw['user'],
        password=kw['password'],
        db=kw['db'],
        # 不设置的话从数据库查到的数据就是乱码
        charset=kw.get('charset', 'utf8'),
        # 自动提交事务，这样在增删改查时就不用每次都提交了
        autocommit=kw.get('autocommit', True),
        maxsize=kw.get('maxsize', 10),
        minsize=kw.get('minsize', 1),
        loop=loop
    )


# select语句，传入sql语句，args占位符，和查询数量size
async def select(sql, args, size=None):
    log(sql, args)
    global __pool
    async with __pool.get() as conn:
        # 获取游标，通过游标操作数据库，游标默认是元祖，这里把他转换为字典
        async with conn.cursor(aiomysql.DictCursor) as cur:
            # 替换的占位符，避免sql直接拼接造成sql注入
            await cur.execute(sql.replace('?', '%s'), args or ())
            # 获取size大小，不给定就是获取全部
            if size:
                rs = await cur.fetchmany(size)
            else:
                rs = await cur.fetchall()
        logging.info('rows returned: %s' % len(rs))
        return rs

# 为增删改统一设置execute函数，因为这三个东东参数相同，就提取一下
async def execute(sql, args, autocommit=True):
    log(sql)
    async with __pool.get() as conn:
        # 如果没有自动提交事务，就手动提交
        if not autocommit:
            await conn.begin()
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql.replace('?', '%s'), args)
                # 获取增删改影响的行数，不用获取select的结果集
                affected = cur.rowcount
            if not autocommit:
                await conn.commit()
        except BaseException as e:
            if not autocommit:
                # 如果提交事务错误，就回滚到事务之前
                await conn.rollback()
            raise
        return affected

# 用来计算要拼接多少个占位符
def create_args_string(num):
    L = []
    for n in range(num):
        L.append('?')
    return ', '.join(L)


# 用于保存数据库的列名和基类的类型
class Field(object):

    def __init__(self, name, column_type, primary_key, default):
        self.name = name #表名称
        self.column_type = column_type #列类型
        self.primary_key = primary_key #是否主键
        self.default = default

    def __str__(self):
        return '<%s, %s:%s>' % (self.__class__.__name__, self.column_type, self.name)

# 保存列名的数据类型
class StringField(Field):

    def __init__(self, name=None, primary_key=False, default=None, ddl='varchar(100)'):
        super().__init__(name, ddl, primary_key, default)

class BooleanField(Field):

    def __init__(self, name=None, default=False):
        super().__init__(name, 'boolean', False, default)

class IntegerField(Field):

    def __init__(self, name=None, primary_key=False, default=0):
        super().__init__(name, 'bigint', primary_key, default)

class FloatField(Field):

    def __init__(self, name=None, primary_key=False, default=0.0):
        super().__init__(name, 'real', primary_key, default)

class TextField(Field):

    def __init__(self, name=None, default=None):
        super().__init__(name, 'text', False, default)

# 继承于基类model的子类user可以通过这个方法扫描映射关系，并保存到自身的类属性中
class ModelMetaclass(type):

    def __new__(cls, name, bases, attrs):
        # 排除model类本身，因为model没有可处理的
        if name == 'Model':
            return type.__new__(cls, name, bases, attrs)
        # 获取表名，如果获取不到，把类名当表名
        tableName = attrs.get('__table__', None) or name
        logging.info('found model: %s (table: %s)' % (name, tableName))

        # 获取所有类属性和主键名
        mappings = dict() #存储属性名和字段信息的映射关系
        fields = [] #存储所有非主键的属性
        primaryKey = None #存储主键属性
        for k, v in attrs.items(): #遍历所有属性，k为属性名，v为对应的字段信息
            if isinstance(v, Field): #如果v是自己定义的字段类型
                logging.info('  found mapping: %s ==> %s' % (k, v))
                mappings[k] = v #存储映射关系
                if v.primary_key: #如果该属性是主键
                    if primaryKey: #如果primaryKey保存了主键，说明主键已经找到，主键重复
                        raise RuntimeError('Duplicate primary key for field: %s' % k)
                    primaryKey = k
                else:
                    fields.append(k)   #如果不是主键，存储到fields中去
        if not primaryKey:          #如果遍历了所有属性都没有找到主键，则主键未定义
            raise RuntimeError('Primary key not found.')
        for k in mappings.keys():
            attrs.pop(k) # 清空attrs里的属性

        # 将fields中的属性名以`属性名`的方式装饰起来
        escaped_fields = list(map(lambda f: '`%s`' % f, fields))
        # 重新设置attrs，类的属性方法都放在fields，主键都放在primarykey
        attrs['__mappings__'] = mappings  # 保存属性和字段信息的映射关系
        attrs['__table__'] = tableName     #保存表名
        attrs['__primary_key__'] = primaryKey  # 主键属性名
        attrs['__fields__'] = fields  # 除主键外的属性名

        #构造默认的增删改查语句
        attrs['__select__'] = 'select `%s`, %s from `%s`' % (primaryKey, ', '.join(escaped_fields), tableName)
        attrs['__insert__'] = 'insert into `%s` (%s, `%s`) values (%s)' % (
        tableName, ', '.join(escaped_fields), primaryKey, create_args_string(len(escaped_fields) + 1))
        attrs['__update__'] = 'update `%s` set %s where `%s`=?' % (
        tableName, ', '.join(map(lambda f: '`%s`=?' % (mappings.get(f).name or f), fields)), primaryKey)
        attrs['__delete__'] = 'delete from `%s` where `%s`=?' % (tableName, primaryKey)
        return type.__new__(cls, name, bases, attrs)


# orm的基类model
class Model(dict, metaclass=ModelMetaclass):

    def __init__(self, **kw):
        super(Model, self).__init__(**kw)

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(r"'Model' object has no attribute '%s'" % key)

    def __setattr__(self, key, value):
        self[key] = value

    def getValue(self, key):
        return getattr(self, key, None)

    def getValueOrDefault(self, key):
        value = getattr(self, key, None)
        if value is None:                       # 如果没有找到value
            field = self.__mappings__[key]      # 从mapping集合映射中找
            if field.default is not None:
                value = field.default() if callable(field.default) else field.default
                logging.debug('using default value for %s: %s' % (key, str(value)))
                setattr(self, key, value)
        return value



    @classmethod # 把类中的方法声明为类方法
    # 查找多条记录
    async def findAll(cls, where=None, args=None, **kw):
        ' find objects by where clause. '
        sql = [cls.__select__]
        # 如果where查询条件存在
        if where:
            sql.append('where')     # 添加where关键字
            sql.append(where)       # 拼接where查询条件
        if args is None:
            args = []
        orderBy = kw.get('orderBy', None)       # 获取kw里的orderby查询条件
        if orderBy:                             # 如果存在orderby
            sql.append('order by')              # 拼接orderby字符串
            sql.append(orderBy)                 # 拼接orderby查询条件
        limit = kw.get('limit', None)           # 获取limit查询条件
        if limit is not None:
            sql.append('limit')
            if isinstance(limit, int):          # 如果limit是int型
                sql.append('?')                 # sql拼接一个占位符
                args.append(limit)              # 将limit添加进参数列表，之所以添加参数列表之后再进行整合是为了防止sql注入
            elif isinstance(limit, tuple) and len(limit) == 2:      # 如果limit是一个tuple类型并且长度是2
                sql.append('?, ?')              # sql语句拼接两个占位符
                args.extend(limit)              # 将limit添加进参数列表
            else:
                raise ValueError('Invalid limit value: %s' % str(limit))
        rs = await select(' '.join(sql), args)      # 将args参数列表注入sql语句之后，传递给select函数进行查询并返回查询结果
        return [cls(**r) for r in rs]

    @classmethod
    # 查询某个字段的数量
    async def findNumber(cls, selectField, where=None, args=None):
        ' find number by select and where. '
        sql = ['select %s _num_ from `%s`' % (selectField, cls.__table__)]
        if where:
            sql.append('where')
            sql.append(where)
        rs = await select(' '.join(sql), args, 1)
        if len(rs) == 0:
            return None
        return rs[0]['_num_']


    # 通过主键查找
    @classmethod
    async def find(cls, pk):
        ' find object by primary key. '
        rs = await select('%s where `%s`=?' % (cls.__select__, cls.__primary_key__), [pk], 1)
        if len(rs) == 0:
            return None
        return cls(**rs[0])

    # 保存实例到数据库
    async def save(self):
        # 将__fields__保存的除主键外的所有属性一次传递到getValueOrDefault函数中获取值
        args = list(map(self.getValueOrDefault, self.__fields__))
        # 获取主键值
        args.append(self.getValueOrDefault(self.__primary_key__))
        # 执行insertsql语句
        rows = await execute(self.__insert__, args)
        if rows != 1:
            logging.warn('failed to insert record: affected rows: %s' % rows)

    # 更新数据库数据
    async def update(self):
        args = list(map(self.getValue, self.__fields__))
        args.append(self.getValue(self.__primary_key__))
        rows = await execute(self.__update__, args)
        if rows != 1:
            logging.warn('failed to update by primary key: affected rows: %s' % rows)

    # 删除数据
    async def remove(self):
        args = [self.getValue(self.__primary_key__)]
        rows = await execute(self.__delete__, args)
        if rows != 1:
            logging.warn('failed to remove by primary key: affected rows: %s' % rows)


