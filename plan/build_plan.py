# -*- coding: utf-8 -*-
import json, datetime
from items_01 import ITEMS1
from items_02 import ITEMS2
from items_03 import ITEMS3
items = (ITEMS1+ITEMS2+ITEMS3)
TAGS = {'m':'#تمرین #فیتنس #بدنسازی #تناسب_اندام #ورزش_در_خانه #fitplus',
        'f':'#تغذیه_سالم #تغذیه_ورزشی #رژیم_غذایی #سبک_زندگی_سالم #فیتنس #fitplus',
        'r':'#رسپی_سالم #غذای_سالم #آشپزی_سالم #برنامه_غذایی #تغذیه_سالم #fitplus'}
CTA = {'m':'برنامه‌ی تمرین مخصوص خودت تو اپ fitplus — لینک بایو 📲','f':'برنامه‌ی غذایی مخصوص خودت تو اپ fitplus — لینک بایو 📲','r':'برنامه‌ی غذایی مخصوص خودت تو اپ fitplus — لینک بایو 📲'}
start = datetime.date(2026,11,9)
out = []
for i, it in enumerate(items[:60]):
    day = start + datetime.timedelta(days=i//2)
    slot = '20:00' if i % 2 == 0 else '16:00'   # روز اول: ۱۶:۰۰ اووکادو قبلاً هست؛ از ۲۰:۰۰ شروع
    if i % 2 == 0: pass
    bullets = '\n'.join('• %s: %s' % (h, p) for h, p in it['s'][:4])
    cap = '%s\n\n%s\n\n%s\n\nسیو کن 🔖 و برای رفیق تمرینیت بفرست 💜\n\n%s\n\n%s' % (it['k'], bullets, it['q'], CTA[it['c']], TAGS[it['c']])
    out.append(dict(n=i+1, date=str(day), time=slot, cat=it['c'], title=it['t'], slides=it['s'], scenes=it['sc'], caption=cap))
# زمان‌بندی درست: ۱۶:۰۰ و ۲۰:۰۰ هر روز؛ آیتم ۱ = ۹ نوامبر ۲۰:۰۰ ، آیتم ۲ = ۱۰ نوامبر ۱۶:۰۰ ...
slots=[]
d=datetime.date(2026,11,9); t='20:00'
for i in range(len(out)):
    slots.append((str(d),t))
    if t=='20:00': d+=datetime.timedelta(days=1); t='16:00'
    else: t='20:00'
for o,(dd,tt) in zip(out,slots): o['date'],o['time']=dd,tt
json.dump(out, open('plan.json','w'), ensure_ascii=False, indent=1)
print(len(items), len(out), out[0]['date'], out[0]['time'], out[-1]['date'], out[-1]['time'])
print(sum(1 for o in out if len(o['scenes'])!=7), sum(1 for o in out if len(o['slides'])!=5), len(set(o['title'] for o in out)))
