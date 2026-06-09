path = "D:/hanako/investment-system/web/mw-signals/index.html"
with open(path, "r", encoding="utf-8") as f:
    t = f.read()

t = t.replace("score_d+'/5", "score_d+'/15")
t = t.replace("0/5 (跌幅", "0/15 (跌幅")
t = t.replace("score_i2+'/10", "score_i2+'/15")
t = t.replace("0/10'", "0/15'")
t = t.replace("score_sig+'/25", "score_sig+'/10")
t = t.replace("0/25'", "0/10'")

with open(path, "w", encoding="utf-8") as f:
    f.write(t)
print("done")
