import string
from datetime import timedelta

def is_valid_userid(userid: str) -> bool:
    """验证用户ID格式是否有效"""
    if not userid or len(userid.strip()) == 0:
        return False
    userid = userid.strip()
    if len(userid) > 64:
        return False
    allowed_chars = string.ascii_letters + string.digits + "_-:@."
    return all(c in allowed_chars for c in userid)

def format_timedelta(td: timedelta) -> str:
    """格式化时间差"""
    total_seconds = int(td.total_seconds())
    minutes, seconds = divmod(total_seconds, 60)
    if minutes > 0 and seconds > 0:
        return f"{minutes}分{seconds}秒"
    elif minutes > 0:
        return f"{minutes}分"
    else:
        return f"{seconds}秒"
