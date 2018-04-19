from orm import Model, StringField, IntegerField, create_pool
import asyncio

class User(Model):
    __table__ = 'users'

    id = IntegerField(primary_key=True)
    name = StringField()

async def main(loop):
    await create_pool(loop, **database)
    user = User()
    user.id = 123
    user.name = 'Tony'
    await user.save()
    return user.name

loop = asyncio.get_event_loop()
database = {
    'host':'127.0.0.1', #数据库的地址
    'user':'root',
    'password':'admin',
    'db':'blog'
}

task = asyncio.ensure_future(main(loop))

res = loop.run_until_complete(task)
print(res)