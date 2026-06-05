"""v4: for single-sub repeat candidates, determine CHOSEN vs EDITED vs DEFAULT-REPEAT vs REAL.

Error criteria (product-rules / Recharge Error Scan Learnings):
- box_contents property on box line  -> customer CHOSE items       => FALSE (not error)
- Recharge bundle_selections present  -> customer EDITED/customized => FALSE (not error)
- Fixed Selections in product_title   -> fixed                      => FALSE
- else (default recipe repeated, rotation not applied)              => ROTATION-REPEAT (not a dup error, but rotation bug)
- only REAL if none of the above and food items genuinely off-recipe
"""
import sys, json, time, re
sys.path.insert(0, "C:/Users/Work/Claude Projects/AppyHour/AppyHourMCP")
sys.path.insert(0, "C:/Users/Work/Claude Projects/AppyHour/InventoryReorder")
from utils import get_shopify_auth
import requests

# 19 single-sub candidates: current order -> email
CAND = {
 "#148985":"jenndela@gmail.com","#148801":"dimoebus@gmail.com","#147130":"kathyg233@comcast.net",
 "#147475":"spanyr@gmail.com","#147822":"anselee@gmail.com","#148971":"hrolinski@gmail.com",
 "#147905":"esmeemuffin@gmail.com","#148576":"dpeters81@comcast.net","#148655":"kjanashennigan@gmail.com",
 "#148920":"rmpittman1972@yahoo.com","#148927":"lroswog@hotmail.com","#147494":"brasela06@gmail.com",
 "#147779":"wnoell@ymail.com","#147545":"adellel@gmail.com","#148167":"wendi.evenson@yahoo.com",
 "#149105":"krystynh7@gmail.com","#147360":"qhoward1@yahoo.com","#147743":"goodnightmare123@gmail.com",
 "#148574":"mrenna100@yahoo.com",
}

SETTINGS=r"C:\Users\Work\Claude Projects\AppyHour\InventoryReorder\dist\inventory_reorder_settings.json"
rc=json.load(open(SETTINGS,encoding="utf-8"))
RH={"X-Recharge-Access-Token":rc["recharge_api_token"],"Content-Type":"application/json","X-Recharge-Version":"2021-11"}
def rg(p,par=None,retries=5):
    last=None
    for a in range(retries):
        try:
            r=requests.get("https://api.rechargeapps.com"+p,headers=RH,params=par,timeout=30)
            if r.status_code==429: time.sleep(int(r.headers.get("retry-after","5"))); continue
            r.raise_for_status(); return r.json()
        except requests.exceptions.Timeout as e: last=e; time.sleep(2*(a+1))
        except Exception as e: last=e; time.sleep(2)
    raise last

base,headers=get_shopify_auth()
print(f"{'ORDER':<9}{'box_contents':<13}{'bundle_sel':<11}{'fixed':<6}VERDICT")
out=[]
for name,email in CAND.items():
    # shopify order
    o=requests.get(f"{base}/orders.json",headers=headers,params={"name":name,"status":"any","fields":"id,name,line_items"},timeout=30).json()["orders"]
    o=next((x for x in o if x["name"]==name),None)
    has_bc=False; box_sku=None
    if o:
        for li in o["line_items"]:
            sku=(li.get("sku") or "").strip()
            if sku.startswith("AHB-") and ("CUST" in sku or sku in ("AHB-MED","AHB-LGE","AHB-CMED")):
                box_sku=sku
                props={p["name"].lower() for p in (li.get("properties") or [])}
                if "box_contents" in props or "box contents" in props: has_bc=True
    # recharge sub + bundle_selections + fixed
    custs=rg("/customers",{"email":email,"limit":1}).get("customers",[]); time.sleep(0.2)
    has_bs=False; fixed=False
    if custs:
        cid=str(custs[0]["id"])
        subs=[s for s in rg("/subscriptions",{"customer_id":cid,"limit":100}).get("subscriptions",[]) if (s.get("status","") or "").lower()=="active"]
        time.sleep(0.2)
        for s in subs:
            if "fixed selection" in (s.get("product_title","") or "").lower(): fixed=True
            bs=rg("/bundle_selections",{"purchase_item_ids":s["id"],"limit":10}).get("bundle_selections",[]); time.sleep(0.2)
            if any(b.get("items") for b in bs): has_bs=True
    if has_bc: v="FALSE chosen(box_contents)"
    elif has_bs: v="FALSE edited(bundle_sel)"
    elif fixed: v="FALSE fixed-selections"
    else: v="ROTATION-REPEAT (default recipe, not a dup error)"
    print(f"{name:<9}{str(has_bc):<13}{str(has_bs):<11}{str(fixed):<6}{v}  box={box_sku}")
    out.append((name,email,v,box_sku))
    time.sleep(0.2)

reals=[x for x in out if x[2].startswith("ROTATION") or x[2].startswith("REAL")]
print(f"\nFALSE (chosen/edited/fixed): {len(out)-len(reals)}")
print(f"Not-chosen (rotation-repeat or real): {len(reals)}")
for n,e,v,b in reals: print(f"  {n:<9} {e:<32} {b}")
