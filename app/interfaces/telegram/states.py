from aiogram.fsm.state import State, StatesGroup


class ConnectForm(StatesGroup):
    name = State()
    host = State()
    port = State()
    username = State()
    auth_type = State()
    password = State()
    key_file = State()
