import os
import datetime
from jinja2 import Environment, FileSystemLoader

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

TZ_SHANGHAI = datetime.timezone(datetime.timedelta(hours=8))


def to_local(dt: datetime.datetime | None) -> datetime.datetime | None:
    """将 UTC 时间转为北京时间（Asia/Shanghai, UTC+8）"""
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(TZ_SHANGHAI)
    return dt.replace(tzinfo=datetime.timezone.utc).astimezone(TZ_SHANGHAI)


def today_start_utc() -> datetime.datetime:
    """返回北京时间今天 00:00:00 对应的 UTC 时间（用于数据库查询）"""
    now_beijing = datetime.datetime.now(datetime.timezone.utc).astimezone(TZ_SHANGHAI)
    midnight_beijing = now_beijing.replace(hour=0, minute=0, second=0, microsecond=0)
    midnight_utc = midnight_beijing.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return midnight_utc


def cron_desc(expr: str) -> str:
    """将 cron 表达式转为人类可读描述"""
    parts = expr.strip().split()
    if len(parts) != 5:
        return expr

    minute, hour, day, month, weekday = parts

    if hour == "*" and minute != "*":
        h = "每小时"
    elif "/" in hour:
        n = hour.split("/")[1]
        h = f"每{n}小时"
    elif "," in hour:
        h = f"{hour}点"
    else:
        h = f"每天{hour}:{minute.zfill(2)}"

    if weekday != "*":
        days = {"0": "日", "1": "一", "2": "二", "3": "三", "4": "四", "5": "五", "6": "六"}
        w = ",".join(days.get(d, d) for d in weekday.split(","))
        h += f" 周{w}"

    return h


templates_env = Environment(
    loader=FileSystemLoader(os.path.join(BASE_DIR, "templates")),
    cache_size=1,
)
templates_env.globals["cron_desc"] = cron_desc
templates_env.globals["to_local"] = to_local
