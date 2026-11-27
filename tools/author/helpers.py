from gen import O
def T(k, box, text, p=None, w=None, ml=False, conf=None): return O(k, "text", box, p, text=text, fg="auto", weight=w, multiline=ml, conf=conf)
def IC(k, box, name, p=None, conf=None, bg=None, shape="rectangle"): return O(k, "icon", box, p, fg="auto", icon=name, conf=conf, bg=bg, shape=shape)
def BTN(k, box, p=None, type="icon_button", shape="circle", bg="auto", text=None, state="none", conf=None, rt=None, sem=None):
    return O(k, "button", box, p, type=type, shape=shape, bg=bg, text=None, state=state, conf=conf, rt=rt, sem=sem)
def CARD(k, box, p=None, bg="auto", rt=None, conf=None, sem=None, shape="rectangle"): return O(k, "card", box, p, bg=bg, rt=rt, conf=conf, sem=sem, shape=shape)
