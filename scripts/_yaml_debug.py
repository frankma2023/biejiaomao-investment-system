import yaml
d=yaml.safe_load(open('D:/hanako/investment-system/config/index_style.yaml',encoding='utf-8'))
cats = d.get('categories', {})
for k in cats:
    items = cats[k]
    if isinstance(items, list):
        print(f'{k}: {len(items)} items, first: {items[0] if items else "empty"}')
    else:
        print(f'{k}: {type(items).__name__}')
