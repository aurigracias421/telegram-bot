import os,json,time,uuid,logging,asyncio,hashlib,requests
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler,MessageHandler,CallbackQueryHandler,filters,ContextTypes

logging.basicConfig(level=logging.INFO,format="%(asctime)s [%(levelname)s] %(message)s",datefmt="%H:%M:%S")
log=logging.getLogger("Bot")

class Card:
    @classmethod
    def parse(cls,r):
        p=r.strip().split("|")
        if len(p)!=4:raise ValueError("Formato: CC|MM|AAAA|CVV")
        n=p[0].replace(" ","").replace("-","");m=p[1].strip();y=p[2].strip();c=p[3].strip()
        if not n.isdigit() or len(n)<13:raise ValueError("Numero invalido")
        if not m.isdigit() or not 1<=int(m)<=12:raise ValueError("Mes invalido")
        if not y.isdigit() or len(y)not in(2,4):raise ValueError("Anio invalido")
        if not c.isdigit() or len(c)<3:raise ValueError("CVV invalido")
        if len(y)==2:y="20"+y
        return type("obj",(),{"number":n,"exp_month":m,"exp_year":y,"cvv":c,"raw":r,"bin":n[:6],"last4":n[-4:],"masked":f"{n[:6]}******{n[-4:]}","brand":"Visa" if n.startswith("4")else"Mastercard"if n.startswith("5")and n[1]in"12345"else"Amex"if n[:2]in("34","37")else"Other"})()

class StripeTester:
    def __init__(self,ak):self.api_key=ak
    def test(self,card,amt=50,cur="usd"):
        import time as t;start=t.time()
        s=requests.Session();s.headers.update({"Authorization":f"Bearer {self.api_key}","Content-Type":"application/x-www-form-urlencoded"})
        try:
            r=s.post("https://api.stripe.com/v1/payment_methods",data={"type":"card","card[number]":card.number,"card[exp_month]":card.exp_month,"card[exp_year]":card.exp_year,"card[cvc]":card.cvv},timeout=30)
            if r.status_code!=200:return{"ok":False,"err":r.json().get("error",{}).get("message",r.text),"ms":(t.time()-start)*1000}
            pm=r.json()["id"]
            r2=s.post("https://api.stripe.com/v1/payment_intents",data={"amount":str(amt),"currency":cur,"payment_method":pm,"confirm":"true","off_session":"true","description":f"HB-{card.bin}"},headers={"Idempotency-Key":hashlib.sha256(f"{card.number}{amt}{uuid.uuid4()}".encode()).hexdigest()},timeout=30)
            d=r2.json();ok=r2.status_code==200 and d.get("status")=="succeeded"
            rid,rs=None,None
            if d.get("id"):
                rr=s.post("https://api.stripe.com/v1/refunds",data={"payment_intent":d["id"],"amount":str(amt)},timeout=30)
                rs=rr.status_code==200;rid=rr.json().get("id")if rs else None
                if not rs:
                    try:s.post(f"https://api.stripe.com/v1/payment_intents/{d['id']}/cancel",timeout=5)
                    except:pass
            return{"ok":ok,"tx":d.get("id"),"status":d.get("status"),"err":None if ok else d.get("error",{}).get("message","?"),"refund":rs,"refund_id":rid,"ms":(t.time()-start)*1000}
        except Exception as e:return{"ok":False,"err":str(e),"ms":(t.time()-start)*1000}

class Config:
    def __init__(self):
        self.data=json.load(open("config.json"))if os.path.exists("config.json")else{}
    def save(self):json.dump(self.data,open("config.json","w"),indent=2)
    def ok(self,g):return self.data.get(g,{}).get("ok")
    def get(self,g):return self.data.get(g,{})
    def set(self,g,c):self.data[g]=c;self.data[g]["ok"]=True;self.save()

class Bot:
    def __init__(self,tok):
        self.cfg=Config();self.results=[];self.stripe=None
        if self.cfg.ok("stripe"):self.stripe=StripeTester(self.cfg.get("stripe")["api_key"])
        self.app=Application.builder().token(tok).build()
        self.app.add_handler(CommandHandler("start",self.start))
        self.app.add_handler(CommandHandler("help",self.help))
        self.app.add_handler(CommandHandler("setup",self.setup))
        self.app.add_handler(CommandHandler("config",self.config))
        self.app.add_handler(CommandHandler("test",self.test))
        self.app.add_handler(CommandHandler("clear",self.clear))
        self.app.add_handler(CallbackQueryHandler(self.cb))
        self.app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,self.txt))
    async def reply(self,u,m):await u.message.reply_text(m)
    async def start(self,u,c):await self.reply(u,"HackerBot - Payment Tester\n\nPrueba tarjetas contra Stripe\nMicro-cargos con reembolso automatico\n\n/setup - Configurar API key\n/test 4111111111111111|12|2028|123 - Probar tarjeta\n/help - Ayuda")
    async def help(self,u,c):await self.reply(u,"Comandos:\n/start - Inicio\n/help - Ayuda\n/setup - Configurar Stripe\n/config - Ver configuracion\n/test CC|MM|AAAA|CVV - Probar tarjeta\n/clear - Limpiar resultados")
    async def config(self,u,c):
        if not self.cfg.ok("stripe"):await self.reply(u,"Stripe no configurado. Usa /setup");return
        d=self.cfg.get("stripe")
        await self.reply(u,f"Stripe configurado. API Key: {d.get('api_key','')[:8]}...")
    async def setup(self,u,c):
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("Stripe",callback_data="s")],[InlineKeyboardButton("Cancelar",callback_data="x")]])
        await u.message.reply_text("Selecciona pasarela:",reply_markup=kb)
    async def cb(self,u,c):
        q=u.callback_query;await q.answer()
        if q.data=="x":await q.edit_message_text("Cancelado.");return
        if q.data=="s":c.user_data["step"]="k";await q.edit_message_text("Stripe\n\nEnvia tu Secret Key (sk_...):")
    async def txt(self,u,c):
        if c.user_data.get("step")!="k":return
        t=u.message.text.strip()
        if not t.startswith("sk_"):await self.reply(u,"Error: Debe empezar con sk_");return
        self.cfg.set("stripe",{"api_key":t});self.stripe=StripeTester(t);c.user_data["step"]=None
        await self.reply(u,"Stripe configurado correctamente!\nUsa /test 4111111111111111|12|2028|123 para probar")
    async def test(self,u,c):
        if not self.stripe:await self.reply(u,"Usa /setup primero");return
        if not c.args:await self.reply(u,"Uso: /test 4111111111111111|12|2028|123");return
        try:card=Card.parse(" ".join(c.args))
        except ValueError as e:await self.reply(u,f"Error: {e}");return
        msg=await u.message.reply_text(f"Probando {card.masked}...")
        r=self.stripe.test(card);self.results.append(r)
        estado="OK"if r["ok"]else"FAIL"
        refund=""
        if r.get("refund")==True:refund=" | Reembolsado"
        elif r.get("refund")==False:refund=" | Reembolso fallo"
        await msg.edit_text(f"{estado} - Stripe{refund}\nBIN: {card.bin} ({card.brand})\nStatus: {r.get('status') or str(r.get('err','?'))[:60]}\nTX: {r.get('tx','N/A')}\n{'' if not r.get('refund_id') else 'Refund: '+r['refund_id']}\nTiempo: {r.get('ms',0):.0f}ms")
       async def clear(self,u,c):self.results=[];await self.reply(u,"Resultados limpiados.")

    def run(self):
        log.info("Bot iniciado correctamente!")
        self.app.run_polling(allowed_updates=Update.ALL_TYPES)


TOKEN = os.getenv("BOT_TOKEN")

Bot(TOKEN).run()
