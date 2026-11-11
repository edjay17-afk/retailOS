from pathlib import Path
import re, colorsys, shutil, json
from playwright.sync_api import sync_playwright
R=Path(__file__).resolve().parent
SRC=R.parent/'redesign-v3'
themes=[
 dict(key='navy',name='Midnight navy + amber',note='Recommended · balanced and polished',hue=215,dark='#192B45',primary='#253C5C',accent='#F3C969',accentink='#604816',bg='#F5F3ED',card='#FFFEFA',soft='#E8EDF4',chart='#7188AD',chartfill='#E8EEF6',ink='#26364D',mute='#6F7B8C',warn='#F6ECD5'),
 dict(key='charcoal',name='Charcoal + copper',note='Warm · grounded · understated',hue=25,dark='#292827',primary='#3A3430',accent='#D89A72',accentink='#553521',bg='#F4F0EB',card='#FFFCF8',soft='#ECE3DC',chart='#AF7958',chartfill='#F3E7DD',ink='#39312C',mute='#83766E',warn='#F3E5D4'),
 dict(key='indigo',name='Indigo + lavender',note='Contemporary · expressive · soft',hue=242,dark='#29294F',primary='#3E3E70',accent='#C9BDF0',accentink='#4B3B76',bg='#F3F2F7',card='#FEFDFF',soft='#EAE7F4',chart='#8C80B9',chartfill='#EEEBF7',ink='#34334E',mute='#7C7890',warn='#F4ECD9'),
]
pages=['dashboard','replenishment','purchase-order']
base=(SRC/'style.css').read_text(encoding='utf-8')
def recolor(value,t):
 raw=value[1:];alpha=raw[6:] if len(raw)==8 else '';raw=raw[:6]
 if len(raw)!=6:return value
 r,g,b=[int(raw[i:i+2],16)/255 for i in (0,2,4)]
 h,l,s=colorsys.rgb_to_hls(r,g,b)
 rgb=colorsys.hls_to_rgb(t['hue']/360,l,min(s*.7,.22))
 return '#'+''.join(f'{round(x*255):02x}' for x in rgb)+alpha
for t in themes:
 d=R/t['key'];d.mkdir(exist_ok=True);(d/'screenshots').mkdir(exist_ok=True)
 shutil.copyfile(SRC/'mark.svg',d/'mark.svg')
 css=re.sub(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\b',lambda m:recolor(m[0],t),base)
 css+=f'''
body{{background:{t['bg']};color:{t['ink']}}}
.rail{{background:{t['dark']}}}.rail .selected{{background:{t['accent']};color:{t['accentink']}}}
.hero,.selection,.total{{background:{t['primary']};color:{t['card']}}}
.primary,.bottomnav{{background:{t['dark']};border-color:{t['dark']};color:{t['card']}}}
.panel,.metric{{background:{t['card']}}}.hero .mini-chart i{{background:{t['accent']}}}
.hero .eyebrow,.hero .subline,.hero strong small,.total .eyebrow,.total .small,.total .summary dt,.selection .eyebrow,.selection .line,.selection>small{{color:#c3cbd5}}
.nav .active,.source-switch,.metric .icon-square,.workspace .health,.store-avatar,.brief,.formula,.order-banner,.workflow{{background:{t['soft']}}}
.nav .active,.metric .icon-square,.store-avatar,.source-switch .on{{color:{t['primary']}}}
.nav .badge,.metric .tag,.pill,.fab{{background:{t['accent']};color:{t['accentink']};border-color:{t['accent']}}}
.selection .btn{{background:{t['accent']};color:{t['accentink']}}}.selection .line b{{color:{t['card']}}}
.attention,.receive-card{{background:{t['warn']};border-color:#e3d6bd}}
.attention .eyebrow,.attention p,.receive-card p{{color:#8b7957}}
.attention h2,.receive-card h3{{color:#675430}}
.store-track i,.legend i{{background:{t['chart']}}}.formula .formula-line,.formula .explain{{color:{t['primary']}}}
.product-row.selected{{background:{t['soft']}}}.check.checked{{background:{t['primary']};border-color:{t['primary']}}}
.qty input,.btn{{color:{t['primary']}}}.primary{{color:{t['card']}}}.tabs .on{{color:{t['primary']};border-color:{t['primary']}}}
.health .status{{background:#18824c}}.health-line i{{background:#76a78a}}.health-line i:last-child{{background:#c4d9ca}}
.mobile-selection{{background:{t['soft']}}}.bottomnav .active{{color:{t['accent']}}}
'''
 (d/'style.css').write_text(css,encoding='utf-8')
 for p in pages:
  html=(SRC/f'{p}.html').read_text(encoding='utf-8')
  html=re.sub(r'#[0-9a-fA-F]{6}\b',lambda m:recolor(m[0],t),html)
  # Deliberately distinguish the current series from the previous one.
  html=html.replace(recolor('#75974b',t),t['chart']).replace(recolor('#edf3db',t),t['chartfill']).replace(recolor('#244b36',t),t['primary'])
  html=html.replace('DESIGN CONCEPT 03',t['name'].upper()+' · PALETTE PREVIEW')
  html=html.replace('href="index.html"','href="../index.html"')
  (d/f'{p}.html').write_text(html,encoding='utf-8')
gallery_css='''*{box-sizing:border-box}body{margin:0;background:#f5f4f1;color:#26344a;font:14px/1.5 "Segoe UI",sans-serif}main{max-width:1800px;margin:auto;padding:35px 30px}header{display:flex;justify-content:space-between;align-items:end;margin-bottom:25px}h1{font-size:32px;letter-spacing:-.8px;margin:6px 0 8px}header p{color:#7b8189;margin:0;font-size:12px}.eyebrow{font-size:10px;letter-spacing:2px;color:#7a8188}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:22px}.card{border:1px solid #dedfdc;border-radius:13px;background:white;overflow:hidden}.shot{display:block;background:#eceeeb}.shot img{width:100%;display:block}.meta{padding:18px 20px}.meta h2{font-size:18px;margin:0 0 5px}.meta p{font-size:12px;color:#80858a;margin:0 0 14px}.swatches{display:flex;gap:7px;margin-bottom:17px}.swatches i{display:block;width:30px;height:22px;border:1px solid #00000009;border-radius:5px}.links{display:flex;flex-wrap:wrap;gap:8px 16px}a{color:#45617d;text-decoration:none;font-size:11px}footer{font-size:11px;color:#84878a;margin-top:22px}button{border:1px solid #d1d5d9;background:white;border-radius:7px;padding:9px 13px;color:#4d5e71;cursor:pointer}.mobile .shot img{width:240px;margin:auto}.links span{width:100%;font-size:10px;color:#8d9398;margin-top:4px}@media(max-width:800px){.grid{grid-template-columns:1fr}main{padding:24px 16px}header{display:block}header button{margin-top:15px}.shot img{max-height:600px;object-fit:cover;object-position:top}.mobile .shot img{max-height:none}}'''
cards=''
for t in themes:
 k=t['key'];cards+=f'<section class="card"><a class="shot" href="{k}/dashboard.html"><img src="{k}/screenshots/dashboard-desktop.png" data-desktop="{k}/screenshots/dashboard-desktop.png" data-mobile="{k}/screenshots/dashboard-mobile.png" alt="{t["name"]} dashboard"></a><div class="meta"><h2>{t["name"]}</h2><p>{t["note"]}</p><div class="swatches">'+''.join(f'<i style="background:{t[c]}" title="{t[c]}"></i>' for c in ('dark','primary','bg','accent','chart'))+'</div><div class="links">'+''.join(f'<a href="{k}/{p}.html">{p.replace("-"," ").title()} ↗</a>' for p in pages)+f'<span>Full-size screenshots</span><a href="{k}/screenshots/dashboard-desktop.png">Desktop</a><a href="{k}/screenshots/dashboard-mobile.png">Mobile</a></div></div></section>'
(R/'index.html').write_text(f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Retail OS palette comparison</title><style>{gallery_css}</style></head><body><main><header><div><div class="eyebrow">RETAIL OS / COLOUR STUDY</div><h1>Same design. Three different moods.</h1><p>The approved layout, shown in three alternative palettes. Illustrative data · Live system unchanged.</p></div><button id="device">Show mobile</button></header><div class="grid">{cards}</div><footer>Each palette includes dashboard, replenishment and purchase-order previews, with desktop and mobile screenshots. The current logo is retained for comparison.</footer></main><script>let mobile=false;document.querySelector("#device").onclick=()=>{{mobile=!mobile;document.body.classList.toggle("mobile",mobile);document.querySelectorAll(".shot img").forEach(i=>i.src=i.dataset[mobile?"mobile":"desktop"]);document.querySelector("#device").textContent=mobile?"Show desktop":"Show mobile"}}</script></body></html>',encoding='utf-8')
checks=[]
with sync_playwright() as pw:
 b=pw.chromium.launch(channel='msedge',headless=True);page=b.new_page(device_scale_factor=1)
 for t in themes:
  for mode,w,h in [('desktop',1440,1000),('mobile',390,844)]:
   page.set_viewport_size(dict(width=w,height=h))
   for p in pages:
    page.goto((R/t['key']/f'{p}.html').as_uri())
    checks.append(dict(theme=t['key'],page=p,device=mode,overflow=page.evaluate('document.documentElement.scrollWidth>innerWidth')))
    page.screenshot(path=str(R/t['key']/f'screenshots/{p}-{mode}.png'),full_page=mode=='desktop')
 page.set_viewport_size(dict(width=1800,height=1100));page.goto((R/'index.html').as_uri());page.screenshot(path=str(R/'comparison.png'),full_page=True)
 page.get_by_role('button',name='Show mobile').click();page.screenshot(path=str(R/'comparison-mobile.png'),full_page=True)
 b.close()
(R/'validation.json').write_text(json.dumps(checks,indent=2))
assert not any(x['overflow'] for x in checks)
print('Rendered 18 screenshots and 2 comparison sheets. No horizontal overflow.')
