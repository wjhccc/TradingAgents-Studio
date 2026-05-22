"""
East Money Guba (东方财富股吧) crawler for A-share sentiment analysis.

This module provides functionality to fetch and analyze stock discussion posts
from the East Money Guba platform, with sentiment analysis based on keyword matching.
"""

import re
import json
import time
import logging
from urllib.request import urlopen, Request
from urllib.parse import quote, urlencode
from urllib.error import URLError, HTTPError
from html.parser import HTMLParser
from typing import Optional, Dict, List, Tuple, Any

logger = logging.getLogger(__name__)

__all__ = [
    "fetch_eastmoney_guba_sentiment",
    "_get_stock_code_from_ticker",
    "_guess_board_id",
    "STOCK_CODE_MAPPING",
    "BULLISH_KEYWORDS",
    "BEARISH_KEYWORDS",
]


# =============================================================================
# Stock Code Mapping
# =============================================================================

STOCK_CODE_MAPPING: Dict[str, str] = {
    # Consumer (消费)
    "贵州茅台": "600519",
    "五粮液": "000858",
    "泸州老窖": "000568",
    "美的集团": "000333",
    "格力电器": "000651",
    "海尔智家": "600690",
    "伊利股份": "600887",
    "海天味业": "603288",
    "安琪酵母": "600298",
    "青岛啤酒": "600600",
    "涪陵榨菜": "002507",
    "中炬高新": "600872",
    "古井贡酒": "000596",
    "山西汾酒": "600809",
    "酒鬼酒": "000799",
    "舍得酒业": "600702",
    "水井坊": "600779",
    "今世缘": "603369",
    "口子窖": "603589",
    "老白干酒": "600559",
    "张裕A": "000869",
    "华统股份": "002840",
    "新希望": "000876",
    "温氏股份": "300498",
    "牧原股份": "002714",
    "双汇发展": "000895",
    "龙大美食": "002726",
    "绝味食品": "603517",
    "煌上煌": "002695",
    "周黑鸭": "01458",  # 港股
    "海底捞": "06862",  # 港股
    "九毛九": "09922",  # 港股
    "呷哺呷哺": "00520",  # 港股
    
    # Tech / New Energy (科技/新能源)
    "宁德时代": "300750",
    "比亚迪": "002594",
    "亿纬锂能": "300014",
    "赣锋锂业": "002460",
    "天齐锂业": "002466",
    "华友钴业": "603799",
    "寒锐钴业": "300618",
    "洛阳钼业": "603993",
    "隆基绿能": "601012",
    "通威股份": "600438",
    "阳光电源": "300274",
    "三峡能源": "600905",
    "晶澳科技": "002459",
    "天合光能": "688599",
    "晶科能源": "688223",
    "福斯特": "603806",
    "福莱特": "601865",
    "TCL中环": "002129",
    "中环股份": "002129",
    "京东方A": "000725",
    "京东方B": "200725",
    "海康威视": "002415",
    "大华股份": "002236",
    "科大讯飞": "002230",
    "中科曙光": "603019",
    "浪潮信息": "000977",
    "工业富联": "601138",
    "立讯精密": "002475",
    "歌尔股份": "002241",
    "蓝思科技": "300433",
    "鹏鼎控股": "002938",
    "韦尔股份": "603501",
    "卓胜微": "300782",
    "兆易创新": "603986",
    "汇顶科技": "603160",
    "闻泰科技": "600745",
    "长电科技": "600584",
    "通富微电": "002156",
    "华天科技": "002185",
    "北方华创": "002371",
    "中微公司": "688012",
    "华润微": "688396",
    "士兰微": "600460",
    "斯达半导": "603290",
    "时代电气": "688187",
    "中国中车": "601766",
    "中国中铁": "601390",
    "中国建筑": "601668",
    "中国电建": "601669",
    "中国交建": "601800",
    "中国化学": "601117",
    "中国核建": "601611",
    "中国铁建": "601186",
    "三一重工": "600031",
    "中联重科": "000157",
    "徐工机械": "000425",
    "柳工": "000528",
    "浙江鼎力": "603338",
    "恒立液压": "601100",
    "艾迪精密": "603638",
    "安徽合力": "600761",
    "杭叉集团": "603298",
    "诺力股份": "603611",
    "音飞储存": "603066",
    "今天国际": "300532",
    "兰剑智能": "688557",
    
    # Finance (金融)
    "中国平安": "601318",
    "中国人寿": "601628",
    "中国太保": "601601",
    "新华保险": "601336",
    "中国人保": "601319",
    "招商银行": "600036",
    "兴业银行": "601166",
    "浦发银行": "600000",
    "民生银行": "600016",
    "工商银行": "601398",
    "建设银行": "601939",
    "中国银行": "601988",
    "农业银行": "601288",
    "交通银行": "601328",
    "邮储银行": "601658",
    "中信银行": "601998",
    "光大银行": "601818",
    "华夏银行": "600015",
    "平安银行": "000001",
    "宁波银行": "002142",
    "杭州银行": "600926",
    "南京银行": "601009",
    "江苏银行": "600919",
    "成都银行": "601838",
    "长沙银行": "601577",
    "郑州银行": "002936",
    "青岛银行": "002948",
    "西安银行": "600928",
    "重庆银行": "601963",
    "苏州银行": "002966",
    "紫金银行": "601860",
    "沪农商行": "601825",
    "厦门银行": "601187",
    "齐鲁银行": "601665",
    "北京银行": "601169",
    "上海银行": "601229",
    "华安证券": "600909",
    "国泰君安": "601211",
    "中信证券": "600030",
    "海通证券": "600837",
    "广发证券": "000776",
    "华泰证券": "601688",
    "国信证券": "002736",
    "招商证券": "600999",
    "银河证券": "601881",
    "申万宏源": "000166",
    "中信建投": "601066",
    "中金公司": "601995",
    "光大证券": "601788",
    "方正证券": "601901",
    "长江证券": "000783",
    "兴业证券": "601377",
    "东方证券": "600958",
    "东吴证券": "601555",
    "山西证券": "002500",
    "国元证券": "000728",
    "东北证券": "000686",
    "西南证券": "600369",
    "西部证券": "002673",
    "第一创业": "002797",
    "东方财富": "300059",
    "同花顺": "300033",
    "指南针": "300803",
    "财富趋势": "688318",
    "顶点软件": "603383",
    "金证股份": "600446",
    "恒生电子": "600570",
    "赢时胜": "300377",
    "东方国信": "300166",
    "润和软件": "300339",
    "信雅达": "600571",
    "京北方": "002987",
    "宇信科技": "300674",
    "长亮科技": "300348",
    "天阳科技": "300872",
    "高伟达": "300465",
    "科蓝软件": "300663",
    "中科金财": "002657",
    
    # Healthcare (医药)
    "恒瑞医药": "600276",
    "药明康德": "603259",
    "药明生物": "02269",  # 港股
    "泰格医药": "300347",
    "凯莱英": "002821",
    "康龙化成": "300759",
    "昭衍新药": "603127",
    "药石科技": "300725",
    "美迪西": "688202",
    "博腾股份": "300363",
    "九洲药业": "603456",
    "博瑞医药": "688166",
    "奥翔药业": "603229",
    "天宇股份": "300702",
    "奥锐特": "605116",
    "海翔药业": "002099",
    "京新药业": "002020",
    "恩华药业": "002262",
    "人福医药": "600079",
    "现代制药": "600420",
    "华北制药": "600812",
    "东北制药": "000597",
    "华润三九": "000999",
    "昆药集团": "600422",
    "马应龙": "600993",
    "千金药业": "600479",
    "华润双鹤": "600062",
    "双鹭药业": "002038",
    "舒泰神": "300204",
    "康弘药业": "002773",
    "兴齐眼药": "300573",
    "欧普康视": "300595",
    "爱尔眼科": "300015",
    "通策医疗": "600763",
    "美年健康": "002044",
    "爱博医疗": "688050",
    "昊海生科": "688366",
    "华熙生物": "688363",
    "爱美客": "300896",
    "贝泰妮": "300957",
    "珀莱雅": "603605",
    "丸美股份": "603983",
    "上海家化": "600315",
    "片仔癀": "600436",
    "云南白药": "000538",
    "白云山": "600332",
    "同仁堂": "600085",
    "东阿阿胶": "000423",
    "九芝堂": "000989",
    "太极集团": "600129",
    "中新药业": "600329",
    "天士力": "600535",
    "以岭药业": "002603",
    "步长制药": "603858",
    "康缘药业": "600557",
    "红日药业": "300026",
    "血制品": "002007",
    "华兰生物": "002007",
    "天坛生物": "600161",
    "博雅生物": "300294",
    "卫光生物": "002880",
    "双林生物": "000403",
    "派林生物": "000403",
    "上海莱士": "002252",
    "科华生物": "002022",
    "达安基因": "002030",
    "华大基因": "300676",
    "迪安诊断": "300244",
    "金域医学": "603882",
    "润达医疗": "603108",
    "凯普生物": "300639",
    "热景生物": "688068",
    "东方生物": "688298",
    "安旭生物": "688075",
    "奥泰生物": "688606",
    "万泰生物": "603392",
    "康泰生物": "300601",
    "沃森生物": "300142",
    "智飞生物": "300122",
    "康希诺": "688185",
    "长春高新": "000661",
    "安科生物": "300009",
    "我武生物": "300357",
    "艾德生物": "300685",
    "贝达药业": "300558",
    "微芯生物": "688321",
    "泽璟制药": "688266",
    "百奥泰": "688177",
    "神州细胞": "688520",
    "君实生物": "688180",
    "信达生物": "01801",  # 港股
    "百济神州": "688235",
    "再鼎医药": "09688",  # 港股
    "和黄医药": "00013",  # 港股
    
    # Real Estate (房地产)
    "万科A": "000002",
    "万科": "000002",
    "保利发展": "600048",
    "保利": "600048",
    "招商蛇口": "001979",
    "金地集团": "600383",
    "华侨城A": "000069",
    "华侨城": "000069",
    "华润置地": "01109",  # 港股
    "中国恒大": "03333",  # 港股
    "融创中国": "01918",  # 港股
    "碧桂园": "02007",  # 港股
    "龙湖集团": "00960",  # 港股
    "中国金茂": "00817",  # 港股
    "中国海外发展": "00688",  # 港股
    "绿地控股": "600606",
    "新城控股": "601155",
    "中南建设": "000961",
    "阳光城": "000671",
    "蓝光发展": "600466",
    "华夏幸福": "600340",
    "荣盛发展": "002146",
    "金科股份": "000656",
    "世茂股份": "600823",
    "大悦城": "000031",
    "首开股份": "600376",
    "北京城建": "600266",
    "上海临港": "600848",
    "张江高科": "600895",
    "陆家嘴": "600663",
    "外高桥": "600648",
    "浦东金桥": "600639",
    "外高桥": "600648",
    "苏州高新": "600736",
    "南京高科": "600064",
    "渝开发": "000514",
    "沙河股份": "000014",
    "深物业A": "000011",
    "深深宝A": "000019",
    "深振业A": "000006",
    "天健集团": "000090",
    "深圳控股": "00604",  # 港股
    "合生创展": "00754",  # 港股
    "雅戈尔": "600177",
    "泰禾集团": "000732",
    "中天金融": "000540",
    "皇庭国际": "000056",
    "香江控股": "600162",
    "华联控股": "000036",
    "中炬高新": "600872",
    
    # Energy / Materials (能源/材料)
    "中国石油": "601857",
    "中国石化": "600028",
    "中国海油": "600938",
    "中国神华": "601088",
    "陕西煤业": "601225",
    "兖矿能源": "600188",
    "中煤能源": "601898",
    "潞安环能": "601699",
    "山西焦煤": "000983",
    "平煤股份": "601666",
    "开滦股份": "600997",
    "冀中能源": "000937",
    "阳泉煤业": "600348",
    "盘江股份": "600395",
    "神火股份": "000933",
    "露天煤业": "002128",
    "兰花科创": "600123",
    "华阳股份": "600348",
    "电投能源": "002128",
    "淮北矿业": "600985",
    "川能动力": "000155",
    "山煤国际": "600546",
    "金能科技": "603113",
    "宝丰能源": "600989",
    "卫星化学": "002648",
    "恒力石化": "600346",
    "荣盛石化": "002493",
    "桐昆股份": "601233",
    "新凤鸣": "603225",
    "恒逸石化": "000703",
    "东方盛虹": "000301",
    "三友化工": "600409",
    "中泰化学": "002092",
    "华鲁恒升": "600426",
    "鲁西化工": "000830",
    "扬农化工": "600486",
    "利尔化学": "002258",
    "江山股份": "600389",
    "长青股份": "002391",
    "广信股份": "603599",
    "兴发集团": "600141",
    "湖北宜化": "000422",
    "云天化": "600096",
    "六国化工": "600470",
    "司尔特": "002538",
    "新洋丰": "000902",
    "云图控股": "002539",
    "史丹利": "002588",
    "芭田股份": "002170",
    "四川美丰": "000731",
    "盐湖股份": "000792",
    "藏格矿业": "000792",
    "盐湖股份": "000792",
    "科达制造": "600499",
    "洛阳玻璃": "600876",
    "旗滨集团": "601636",
    "南玻A": "000012",
    "福耀玻璃": "600660",
    "信义玻璃": "00868",  # 港股
    "三棵树": "603737",
    "东方雨虹": "002271",
    "北新建材": "000786",
    "伟星新材": "002372",
    "坚朗五金": "002791",
    "索菲亚": "002572",
    "欧派家居": "603833",
    "顾家家居": "603816",
    "喜临门": "603008",
    "梦百合": "603313",
    "尚品宅配": "300616",
    "金牌厨柜": "603180",
    "好莱客": "603898",
    "皮阿诺": "002853",
    "我乐家居": "603326",
    "顶固集创": "300749",
    "玛格家居": "873376",
    "科凡家居": "873017",
    
    # Indices (指数)
    "沪深300": "000300",
    "上证指数": "000001",
    "深证成指": "399001",
    "创业板指": "399006",
    "科创50": "000688",
    "上证50": "000016",
    "中证500": "000905",
    "中证1000": "000852",
    "沪深300": "000300",
    "富时A50": "03800",  # 港股期货
    "恒生指数": "HSI",
    "恒生国企指数": "HSCEI",
    
    # ETFs
    "沪深300ETF": "510300",
    "上证50ETF": "510050",
    "中证500ETF": "510500",
    "创业板ETF": "159915",
    "科创50ETF": "588000",
    "证券ETF": "512880",
    "军工ETF": "512660",
    "芯片ETF": "512760",
    "新能源车ETF": "515030",
    "光伏ETF": "515790",
    "医药ETF": "512010",
    "消费ETF": "159928",
    "黄金ETF": "518880",
    "教育ETF": "513360",
    "游戏ETF": "516010",
    
    # Auto / Manufacturing
    "上汽集团": "600104",
    "广汽集团": "601238",
    "长安汽车": "000625",
    "长城汽车": "601633",
    "比亚迪": "002594",
    "北汽蓝谷": "600733",
    "东风汽车": "600006",
    "江淮汽车": "600418",
    "一汽解放": "000800",
    "福田汽车": "600166",
    "安凯客车": "000868",
    "中通客车": "000957",
    "宇通客车": "600066",
    "金龙汽车": "600686",
    "小鹏汽车": "09868",  # 港股
    "理想汽车": "02015",  # 港股
    "蔚来": "09866",  # 港股
    "零跑汽车": "09863",  # 港股
    "吉利汽车": "00175",  # 港股
    "潍柴动力": "000338",
    "三花智控": "002050",
    "银轮股份": "002126",
    "中集车辆": "301039",
    "天润工业": "002283",
    "富奥股份": "000030",
    "德尔股份": "300473",
    "川环科技": "300547",
    "蠡湖股份": "300694",
    "宁波华翔": "002048",
    "常熟汽饰": "603035",
    "旭升集团": "603305",
    "文灿股份": "603348",
    "拓普集团": "601689",
    "伯特利": "603596",
    "德赛西威": "002920",
    "华阳集团": "002906",
    "均胜电子": "600699",
    "保隆科技": "603197",
    "耐世特": "01316",  # 港股
    "福耀玻璃": "600660",
    
    # Aviation / Shipping
    "中国国航": "601111",
    "东方航空": "600115",
    "南方航空": "600029",
    "春秋航空": "601021",
    "吉祥航空": "603885",
    "华夏航空": "002928",
    "中远海控": "601919",
    "中远海能": "600026",
    "招商轮船": "601872",
    "中集集团": "000039",
    "中船防务": "600685",
    "中国船舶": "600150",
    "中国重工": "601989",
    "中国动力": "600482",
    "航发动力": "600893",
    "中航沈飞": "600760",
    "中航西飞": "000768",
    "中航高科": "600862",
    "中直股份": "600038",
    "洪都航空": "600316",
    "航发科技": "600391",
    "成飞集成": "002190",
    "中航电子": "600372",
    "中航机电": "002013",
    "航锦科技": "000818",
    "应流股份": "603308",
    "炼石航空": "000697",
    "三角防务": "300775",
    "迈信林": "688685",
    "纵横股份": "688070",
    "观典防务": "688287",
    "广联航空": "300900",
    "安达维尔": "300719",
    
    # Telecom / Internet
    "中国移动": "600941",
    "中国联通": "600050",
    "中国电信": "601728",
    "中兴通讯": "000063",
    "烽火通信": "600498",
    "光迅科技": "002281",
    "中际旭创": "300308",
    "新易盛": "300502",
    "天孚通信": "300394",
    "博创科技": "300548",
    "剑桥科技": "603083",
    "华工科技": "000988",
    "海能达": "002583",
    "东方通信": "600776",
    "东信和平": "002017",
    "紫光股份": "000938",
    "烽火电子": "000561",
    "航天电器": "002025",
    "中航光电": "002179",
    "太辰光": "300570",
    "意华股份": "002897",
    "亨通光电": "600487",
    "中天科技": "600522",
    "长飞光纤": "601869",
    "富通信息": "000836",
    "通光线缆": "300265",
    "日海智能": "002313",
    "高鸿股份": "000851",
    "东方国信": "300166",
    "天源迪科": "300047",
    "思特奇": "300608",
    "天玑科技": "300245",
    "银信科技": "300231",
    "荣科科技": "300290",
    "中科创达": "300496",
    "诚迈科技": "300598",
    "金山办公": "688111",
    "用友网络": "600588",
    "广联达": "002410",
    "石基信息": "002153",
    "宝信软件": "600845",
    "光环新网": "300383",
    "数据港": "603881",
    "奥飞数据": "300738",
    "首都在线": "300846",
    "铜牛信息": "300895",
    "浙大网新": "600797",
    "浪潮软件": "600756",
    "中国软件": "600536",
    "东方通": "300379",
    "宝兰德": "688058",
    "普元信息": "688118",
    "星环科技": "688031",
    "达梦数据": "688692",
    "人大金仓": "688579",
    "神舟通用": "874016",
    "神通数据库": "832034",
    
    # Internet / Platforms
    "阿里巴巴": "09988",  # 港股
    "腾讯控股": "00700",  # 港股
    "京东": "09618",  # 港股
    "美团": "03690",  # 港股
    "拼多多": "PDD",  # 纳斯达克
    "百度": "09888",  # 港股
    "网易": "09999",  # 港股
    "哔哩哔哩": "09626",  # 港股
    "快手": "01024",  # 港股
    "小米": "01810",  # 港股
    "小米集团": "01810",  # 港股
    "舜宇光学": "02382",  # 港股
    "歌尔股份": "002241",
    "字节跳动": "BYTEDANCE",  # 未上市
    "滴滴出行": "DIDI",  # 美股
    "满帮": "YMM",  # 美股
    "Boss直聘": "BZ",  # 美股
    "知乎": "ZH",  # 美股
    "斗鱼": "DOYU",  # 美股
    "虎牙": "HUYA",  # 美股
    "微博": "WB",  # 美股
    "阅文集团": "00772",  # 港股
    "猫眼娱乐": "01896",  # 港股
    "阿里影业": "01060",  # 港股
    "横店影视": "603103",
    "光线传媒": "300251",
    "万达电影": "002739",
    "中国电影": "600977",
    "华策影视": "300133",
    "华谊兄弟": "300027",
    "北京文化": "000802",
    "金逸影视": "002415",
    "幸福蓝海": "300528",
    "上海电影": "601595",
    "博纳影业": "001330",
    "顶点软件": "603383",
    "同花顺": "300033",
    "东方财富": "300059",
    "指南针": "300803",
    "财富趋势": "688318",
    
    # Food & Beverage
    "海天味业": "603288",
    "中炬高新": "600872",
    "千禾味业": "603027",
    "恒顺醋业": "600305",
    "加加食品": "002650",
    "加加酱油": "002650",
    "道道全": "002852",
    "西王食品": "000639",
    "金龙鱼": "300999",
    "道道全": "002852",
    "克明食品": "002661",
    "三全食品": "002216",
    "安井食品": "603345",
    "三全食品": "002216",
    "桃李面包": "603866",
    "元祖股份": "603886",
    "广州酒家": "603043",
    "全聚德": "002186",
    "西安饮食": "000721",
    "金陵饭店": "601007",
    "锦江酒店": "600754",
    "首旅酒店": "600258",
    "华天酒店": "000428",
    "岭南控股": "000524",
    "中青旅": "600138",
    "宋城演艺": "300144",
    "黄山旅游": "600054",
    "峨眉山A": "000888",
    "丽江股份": "002033",
    "桂林旅游": "000978",
    "张家界": "000430",
    "九华旅游": "603199",
    "天目湖": "603136",
    "中科云网": "002306",
    "百胜中国": "09987",  # 港股
    "海底捞": "06862",  # 港股
    "呷哺呷哺": "00520",  # 港股
    "九毛九": "09922",  # 港股
    "海伦司": "09869",  # 港股
    "奈雪的茶": "02150",  # 港股
    "茶颜悦色": "CHAYAN",  # 未上市
    "蜜雪冰城": "MIXUE",  # 拟上市
    "良品铺子": "603719",
    "三只松鼠": "300783",
    "来伊份": "603777",
    "盐津铺子": "002847",
    "甘源食品": "002991",
    "劲仔食品": "003000",
    "有友食品": "603697",
    "祖名股份": "003030",
    "华宝股份": "300741",
    "晨光生物": "300138",
    "莱茵生物": "002166",
    "星湖科技": "600866",
    "保龄宝": "002286",
    "百龙创园": "605016",
    "金禾实业": "002597",
    "新和成": "002001",
    "浙江医药": "600216",
    "花园生物": "300401",
    "金达威": "002626",
    "仙乐健康": "300791",
    "康比特": "833756",
    "西王食品": "000639",
    
    # Others / Miscellaneous
    "中国核电": "601985",
    "中国广核": "003816",
    "长江电力": "600900",
    "华能水电": "600025",
    "国投电力": "600886",
    "川投能源": "600674",
    "桂冠电力": "600236",
    "黔源电力": "002039",
    "桂东电力": "600310",
    "闽东电力": "000993",
    "韶能股份": "000601",
    "深南电A": "000037",
    "赣能股份": "000899",
    "豫能控股": "001896",
    "皖能电力": "000543",
    "湖北能源": "000883",
    "湖南发展": "000722",
    "浙江新能": "600032",
    "江苏新能": "603693",
    "甘肃能源": "000791",
    "云南能投": "002053",
    "贵州燃气": "600903",
    "重庆燃气": "600917",
    "成都燃气": "603053",
    "佛燃能源": "002911",
    "深圳燃气": "601139",
    "新疆火炬": "903080",
    "蓝天燃气": "605368",
    "大众公用": "600635",
    "上海燃气": "603928",
    "深圳能源": "000027",
    "山鹰国际": "600567",
    "玖龙纸业": "02689",  # 港股
    "理文造纸": "02314",  # 港股
    "晨鸣纸业": "000488",
    "太阳纸业": "002078",
    "博汇纸业": "600966",
    "岳阳林纸": "600963",
    "中顺洁柔": "002511",
    "维达国际": "03331",  # 港股
    "恒安国际": "01044",  # 港股
    "金红叶纸业": "GOLD",  # 未上市
    "百亚股份": "003006",
    "可靠股份": "301009",
    "豪悦护理": "605009",
    "壹网壹创": "300792",
    "丽人丽妆": "605136",
    "若羽臣": "003010",
    "凯淳股份": "301001",
    "青木股份": "301110",
    "优趣汇": "02177",  # 港股
    "逸仙电商": "YSG",  # 美股
    "水滴公司": "WDH",  # 美股
    "轻松集团": "QSSG",  # 美股
    "爱回收": "RERE",  # 美股
    "万物新生": "RERE",  # 美股
    "叮咚买菜": "DDL",  # 美股
    "每日优鲜": "MF",  # 美股
    "兴盛优选": "XFSC",  # 未上市
    "美菜": "MEICAI",  # 未上市
    "多点": "Dmall",  # 未上市
    "酒仙网": "JXW",  # 拟上市
    "壹玖壹玖": "1919",  # 新三板
    "华致酒行": "300755",
    "酒便利": "838930",
}


# =============================================================================
# Sentiment Keywords
# =============================================================================

BULLISH_KEYWORDS: List[str] = [
    "买入", "加仓", "满仓", "涨停", "看多", "抄底", "牛市", "上涨",
    "突破", "新高", "暴拉", "主升", "反弹", "护盘", "看好", "做多",
    "趋势", "金叉", "低吸", "布局", "低估", "机会", "必涨", "翻倍",
    "吃肉", "上车", "稳了", "爆发", "大牛", "超级", "强烈推荐",
    "值得", "明天", "继续涨", "冲", "干", "满仓干", "梭哈", "重仓",
    "坚定持有", "坚定看多", "中长期", "价值", "洼地", "黄金坑",
]

BEARISH_KEYWORDS: List[str] = [
    "卖出", "清仓", "止损", "跌停", "看空", "割肉", "熊市", "下跌",
    "破位", "新低", "崩盘", "主力出货", "出货", "跑路", "割", "利空",
    "减持", "套牢", "回撤", "死叉", "逃顶", "风险", "远离", "快跑",
    "止损", "割肉", "血亏", "亏", "跌", "小心", "警告", "危险",
    "逃", "撤", "跑", "别买", "别进", "不要进", "赶紧跑", "快跑",
    "小心为上", "谨慎", "观望", "不建议", "回避", "减仓", "轻仓",
]


# =============================================================================
# HTML Parser for Guba Posts
# =============================================================================

class GubaPostParser(HTMLParser):
    """HTML parser to extract posts from East Money Guba pages."""
    
    def __init__(self):
        super().__init__()
        self.posts: List[Dict[str, Any]] = []
        self._in_post_item = False
        self._current_post: Optional[Dict[str, Any]] = None
        self._current_data: str = ""
        self._in_title = False
        self._in_content = False
        self._in_meta = False
        self._in_read_count = False
        self._in_reply_count = False
        self._tag_stack: List[str] = []
        
    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attrs_dict = dict(attrs)
        self._tag_stack.append(tag)
        
        if "class" in attrs_dict:
            cls = attrs_dict["class"]
            
            if "normalpost" in cls or "listitem" in cls or "article-item" in cls:
                self._in_post_item = True
                self._current_post = {
                    "title": "",
                    "content": "",
                    "author": "",
                    "time": "",
                    "read_count": 0,
                    "reply_count": 0,
                    "click": 0,
                }
            
            if "title" in cls.lower() or "post-title" in cls:
                self._in_title = True
            elif "content" in cls.lower() or "abstract" in cls:
                self._in_content = True
            elif "read" in cls.lower() or "click" in cls.lower():
                self._in_read_count = True
            elif "reply" in cls.lower() or "comment" in cls.lower():
                self._in_reply_count = True
            elif "time" in cls.lower() or "date" in cls.lower() or "post-time" in cls:
                self._in_meta = True
                
        if "href" in attrs_dict:
            href = attrs_dict["href"]
            if self._in_post_item and self._current_post is not None:
                if href.startswith("/"):
                    self._current_post["url"] = f"https://guba.eastmoney.com{href}"
                else:
                    self._current_post["url"] = href
                    
        if tag == "span" and self._in_post_item:
            data_id = attrs_dict.get("data-id", "")
            if data_id:
                self._current_post["post_id"] = data_id
                
    def handle_endtag(self, tag: str) -> None:
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()
            
        if self._in_post_item and self._current_post is not None:
            if self._in_title and tag in ("span", "a", "div", "p", "h3"):
                self._current_post["title"] = self._current_data.strip()
                self._in_title = False
            elif self._in_content and tag in ("span", "div", "p"):
                self._current_post["content"] = self._current_data.strip()
                self._in_content = False
            elif self._in_read_count and tag == "span":
                try:
                    self._current_post["click"] = int(re.sub(r"[^\d]", "", self._current_data))
                except ValueError:
                    pass
                self._in_read_count = False
            elif self._in_reply_count and tag == "span":
                try:
                    self._current_post["reply_count"] = int(re.sub(r"[^\d]", "", self._current_data))
                except ValueError:
                    pass
                self._in_reply_count = False
            elif self._in_meta and tag in ("span", "div", "em"):
                self._current_post["time"] = self._current_data.strip()
                self._in_meta = False
                
        if tag == "li" and self._in_post_item:
            self._posts.append(self._current_post)
            self._in_post_item = False
            self._current_post = None
            
        self._current_data = ""
        
    def handle_data(self, data: str) -> None:
        data = data.strip()
        if data:
            self._current_data = data
            
    def error(self, message: str) -> None:
        logger.warning(f"HTML parsing error: {message}")


def _parse_guba_html_via_regex(html: str) -> List[Dict[str, Any]]:
    """
    Parse Guba HTML page using regex patterns.
    Returns list of post dictionaries.
    """
    posts: List[Dict[str, Any]] = []
    
    # Pattern for post items - various formats
    post_patterns = [
        # Pattern 1: data-itemid format
        r'<div[^>]*class="[^"]*listitem[^"]*"[^>]*data-itemid="(\d+)"[^>]*>.*?<span[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</span>.*?<span[^>]*class="[^"]*l-read[^"]*"[^>]*>(\d+)</span>.*?<span[^>]*class="[^"]*l-reply[^"]*"[^>]*>(\d+)</span>.*?<span[^>]*class="[^"]*time[^"]*"[^>]*>(.*?)</span>',
        # Pattern 2: normalpost format
        r'<div[^>]*class="[^"]*normalpost[^"]*"[^>]*>.*?<a[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</a>.*?<span[^>]*class="[^"]*read[^"]*"[^>]*>(\d+)</span>.*?<span[^>]*class="[^"]*reply[^"]*"[^>]*>(\d+)</span>',
        # Pattern 3: article-item format
        r'<li[^>]*class="[^"]*article-item[^"]*"[^>]*>.*?<span[^>]*class="[^"]*article-title[^"]*"[^>]*>(.*?)</span>.*?<span[^>]*class="[^"]*click[^"]*"[^>]*>(\d+)</span>.*?<span[^>]*class="[^"]*reply[^"]*"[^>]*>(\d+)</span>',
    ]
    
    for pattern in post_patterns:
        matches = re.finditer(pattern, html, re.DOTALL)
        for match in matches:
            try:
                groups = match.groups()
                if len(groups) >= 3:
                    post: Dict[str, Any] = {}
                    if groups[0].isdigit():
                        post["post_id"] = groups[0]
                        post["title"] = _clean_html(groups[1])
                        try:
                            post["click"] = int(groups[2]) if groups[2].isdigit() else 0
                        except (ValueError, IndexError):
                            post["click"] = 0
                        try:
                            post["reply_count"] = int(groups[3]) if len(groups) > 3 and groups[3].isdigit() else 0
                        except (ValueError, IndexError):
                            post["reply_count"] = 0
                        post["time"] = groups[4] if len(groups) > 4 else ""
                    else:
                        post["title"] = _clean_html(groups[0])
                        try:
                            post["click"] = int(groups[1]) if groups[1].isdigit() else 0
                        except (ValueError, IndexError):
                            post["click"] = 0
                        try:
                            post["reply_count"] = int(groups[2]) if len(groups) > 2 and groups[2].isdigit() else 0
                        except (ValueError, IndexError):
                            post["reply_count"] = 0
                        post["time"] = groups[3] if len(groups) > 3 else ""
                    
                    post["content"] = ""
                    post["author"] = ""
                    if post["title"]:
                        posts.append(post)
            except Exception as e:
                logger.debug(f"Regex match error: {e}")
                continue
    
    # Try to extract more data with simpler patterns
    # Extract titles with links
    title_pattern = r'<a[^>]*href="(/list[^"]*stock[^"]*)"[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</a>'
    title_matches = re.finditer(title_pattern, html, re.IGNORECASE)
    
    title_map: Dict[str, str] = {}
    for m in title_matches:
        try:
            url = m.group(1)
            title = _clean_html(m.group(2))
            if title and title not in title_map:
                title_map[title] = url
        except Exception:
            continue
    
    # Extract read/reply counts
    read_pattern = r'<span[^>]*class="[^"]*read[^"]*"[^>]*>(\d+)</span>'
    read_matches = re.findall(read_pattern, html, re.IGNORECASE)
    
    reply_pattern = r'<span[^>]*class="[^"]*reply[^"]*"[^>]*>(\d+)</span>'
    reply_matches = re.findall(reply_pattern, html, re.IGNORECASE)
    
    # Combine data
    for i, title in enumerate(title_map.keys()):
        post: Dict[str, Any] = {
            "title": title,
            "content": "",
            "author": "",
            "time": "",
            "click": 0,
            "reply_count": 0,
        }
        if i < len(read_matches):
            try:
                post["click"] = int(read_matches[i])
            except (ValueError, IndexError):
                pass
        if i < len(reply_matches):
            try:
                post["reply_count"] = int(reply_matches[i])
            except (ValueError, IndexError):
                pass
        posts.append(post)
    
    return posts


def _clean_html(text: str) -> str:
    """Remove HTML tags and entities from text."""
    if not text:
        return ""
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode HTML entities
    text = text.replace("&nbsp;", " ")
    text = text.replace("&amp;", "&")
    text = text.replace("&lt;", "<")
    text = text.replace("&gt;", ">")
    text = text.replace("&quot;", '"')
    text = text.replace("&#39;", "'")
    text = text.replace("&apos;", "'")
    # Remove extra whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# =============================================================================
# HTTP Fetching Functions
# =============================================================================

def _make_request(url: str, timeout: int = 15, retry: int = 2) -> Optional[str]:
    """
    Make HTTP request with proper headers and error handling.
    
    Args:
        url: URL to fetch
        timeout: Request timeout in seconds
        retry: Number of retries on failure
        
    Returns:
        Response content as string, or None on failure
    """
    headers: Dict[str, str] = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    
    last_error: Optional[Exception] = None
    
    for attempt in range(retry):
        try:
            request = Request(url, headers=headers, method="GET")
            with urlopen(request, timeout=timeout) as response:
                content = response.read()
                
                # Try to decode as UTF-8, fallback to other encodings
                try:
                    return content.decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        return content.decode("gbk")
                    except UnicodeDecodeError:
                        try:
                            return content.decode("gb2312")
                        except UnicodeDecodeError:
                            return content.decode("latin-1", errors="ignore")
                            
        except HTTPError as e:
            last_error = e
            logger.warning(f"HTTP Error {e.code} for {url}")
            if e.code == 404:
                return None
            if attempt < retry - 1:
                time.sleep(0.5 * (attempt + 1))
                
        except URLError as e:
            last_error = e
            logger.warning(f"URL Error: {e.reason} for {url}")
            if attempt < retry - 1:
                time.sleep(0.5 * (attempt + 1))
                
        except TimeoutError:
            last_error = TimeoutError(f"Request timeout for {url}")
            logger.warning(f"Timeout for {url}")
            if attempt < retry - 1:
                time.sleep(0.5 * (attempt + 1))
                
        except Exception as e:
            last_error = e
            logger.warning(f"Request failed: {e}")
            if attempt < retry - 1:
                time.sleep(0.5 * (attempt + 1))
    
    logger.error(f"Failed to fetch {url} after {retry} attempts: {last_error}")
    return None


def _fetch_via_search_api(ticker: str, stock_code: str, limit: int = 50) -> List[Dict[str, Any]]:
    """
    Fetch posts using East Money search API.
    
    Args:
        ticker: Stock name/ticker
        stock_code: Stock code
        limit: Maximum number of posts to fetch
        
    Returns:
        List of post dictionaries
    """
    posts: List[Dict[str, Any]] = []
    
    # Build search parameters
    search_params = {
        "uid": "",
        "keyword": stock_code,
        "type": "post",
        "pageindex": 1,
        "pagesize": min(limit, 50),
        "ucode": stock_code,
        "sort": "click",  # Sort by click count
        "order": "desc",
    }
    
    # Encode parameters
    param_json = json.dumps(search_params, ensure_ascii=False)
    encoded_param = quote(param_json)
    
    # Build API URL
    api_url = f"https://search-api-web.eastmoney.com/search/jsonp?cb=jQuery&param={encoded_param}"
    
    response = _make_request(api_url)
    
    if not response:
        return posts
        
    try:
        # Parse JSONP response
        # Remove jQuery wrapper
        json_str = response
        if json_str.startswith("jQuery"):
            json_str = json_str[json_str.index("(") + 1:]
            if json_str.endswith(")"):
                json_str = json_str[:-1]
        
        data = json.loads(json_str)
        
        # Extract posts from result
        if isinstance(data, dict):
            result = data.get("result", {})
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except json.JSONDecodeError:
                    result = {}
            
            if isinstance(result, dict):
                hits = result.get("hits", [])
                if isinstance(hits, dict):
                    hits = hits.get("hits", [])
                    
                for hit in hits[:limit]:
                    if isinstance(hit, dict):
                        source = hit.get("_source", hit)
                        post: Dict[str, Any] = {
                            "title": source.get("title", ""),
                            "content": source.get("content", ""),
                            "author": source.get("author", ""),
                            "time": source.get("ptime", source.get("time", "")),
                            "click": source.get("click", source.get("read", 0)),
                            "reply_count": source.get("reply", source.get("comment", 0)),
                        }
                        if post["title"]:
                            posts.append(post)
                            
    except json.JSONDecodeError as e:
        logger.debug(f"JSON parse error: {e}")
    except Exception as e:
        logger.debug(f"Search API error: {e}")
    
    return posts


def _fetch_via_page_scrape(stock_code: str, board_id: int, page: int = 1, sort: str = "f") -> List[Dict[str, Any]]:
    """
    Fetch posts by scraping Guba pages directly.
    
    Args:
        stock_code: Stock code
        board_id: Board ID (1 for Shanghai, 0 for Shenzhen)
        page: Page number
        sort: Sort order (f=follow/recommend, t=time)
        
    Returns:
        List of post dictionaries
    """
    # Build page URL
    url = f"https://guba.eastmoney.com/list,{stock_code},f_{sort},p_{page}.html"
    
    response = _make_request(url)
    
    if not response:
        return []
    
    posts: List[Dict[str, Any]] = []
    
    # Try to use bs4 if available, otherwise use regex
    try:
        from bs4 import BeautifulSoup
        posts = _parse_with_bs4(response, stock_code)
    except ImportError:
        posts = _parse_guba_html_via_regex(response)
        # Also try alternative regex patterns
        if not posts:
            posts = _parse_guba_simple(response)
    
    return posts


def _parse_with_bs4(html: str, stock_code: str) -> List[Dict[str, Any]]:
    """Parse Guba HTML using BeautifulSoup."""
    posts: List[Dict[str, Any]] = []
    
    try:
        soup = BeautifulSoup(html, "html.parser")
        
        # Find post items - various selectors
        post_items = soup.select(".listitem, .normalpost, .article-item, .post-item, .guba-post")
        
        for item in post_items:
            post: Dict[str, Any] = {
                "title": "",
                "content": "",
                "author": "",
                "time": "",
                "click": 0,
                "reply_count": 0,
            }
            
            # Try different selectors for title
            title_elem = item.select_one(".title, .post-title, .article-title, a[href*='read']")
            if title_elem:
                post["title"] = title_elem.get_text(strip=True)
                # Try to get href
                if title_elem.name == "a":
                    href = title_elem.get("href", "")
                    if href.startswith("/"):
                        post["url"] = f"https://guba.eastmoney.com{href}"
                    else:
                        post["url"] = href
            
            # Try selectors for read/click count
            read_elem = item.select_one(".read, .l-read, .click, .read-count, [class*='read']")
            if read_elem:
                try:
                    text = read_elem.get_text(strip=True)
                    post["click"] = int(re.sub(r"[^\d]", "", text)) if text else 0
                except ValueError:
                    pass
                    
            # Try selectors for reply count
            reply_elem = item.select_one(".reply, .l-reply, .comment, .reply-count, [class*='reply']")
            if reply_elem:
                try:
                    text = reply_elem.get_text(strip=True)
                    post["reply_count"] = int(re.sub(r"[^\d]", "", text)) if text else 0
                except ValueError:
                    pass
                    
            # Try selectors for time
            time_elem = item.select_one(".time, .post-time, .date, [class*='time']")
            if time_elem:
                post["time"] = time_elem.get_text(strip=True)
                
            # Try selectors for author
            author_elem = item.select_one(".author, .username, .nickname, [class*='author']")
            if author_elem:
                post["author"] = author_elem.get_text(strip=True)
                
            if post["title"]:
                posts.append(post)
                
    except Exception as e:
        logger.debug(f"BeautifulSoup parsing error: {e}")
        
    return posts


def _parse_guba_simple(html: str) -> List[Dict[str, Any]]:
    """Simple regex-based parsing for Guba HTML."""
    posts: List[Dict[str, Any]] = []
    
    # Extract all posts from HTML
    # Pattern to match post items
    patterns = [
        # Pattern for <li> items with title
        r'<li[^>]*data-itemid="(\d+)"[^>]*>.*?title="([^"]*)"',
        # Pattern for <a> tags with title
        r'<a[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</a>',
        # Pattern for read/click numbers
        r'class="[^"]*read[^"]*"[^>]*>(\d+)</[^>]+>',
    ]
    
    # More targeted patterns
    # Find all div/li elements that might contain posts
    container_pattern = r'<div[^>]*class="[^"]*(?:listitem|normalpost|post-item)[^"]*"[^>]*>(.*?)</div>'
    containers = re.findall(container_pattern, html, re.DOTALL | re.IGNORECASE)
    
    for container in containers:
        post: Dict[str, Any] = {
            "title": "",
            "content": "",
            "author": "",
            "time": "",
            "click": 0,
            "reply_count": 0,
        }
        
        # Extract title
        title_match = re.search(r'<a[^>]*class="[^"]*title[^"]*"[^>]*>([^<]+)</a>', container, re.IGNORECASE)
        if title_match:
            post["title"] = _clean_html(title_match.group(1))
            
        # Extract read count
        read_match = re.search(r'class="[^"]*read[^"]*"[^>]*>(\d+)</[^>]+>', container, re.IGNORECASE)
        if read_match:
            try:
                post["click"] = int(read_match.group(1))
            except ValueError:
                pass
                
        # Extract reply count
        reply_match = re.search(r'class="[^"]*reply[^"]*"[^>]*>(\d+)</[^>]+>', container, re.IGNORECASE)
        if reply_match:
            try:
                post["reply_count"] = int(reply_match.group(1))
            except ValueError:
                pass
                
        # Extract time
        time_match = re.search(r'class="[^"]*time[^"]*"[^>]*>([^<]+)</[^>]+>', container, re.IGNORECASE)
        if time_match:
            post["time"] = time_match.group(1).strip()
            
        if post["title"]:
            posts.append(post)
    
    return posts


# =============================================================================
# Helper Functions
# =============================================================================

def _get_stock_code_from_ticker(ticker: str) -> Optional[str]:
    """
    Map stock name/ticker to stock code.
    
    Args:
        ticker: Stock name or code
        
    Returns:
        Stock code if found, None otherwise
    """
    if not ticker:
        return None
        
    ticker = ticker.strip()
    
    # If it's already a 6-digit code, return as-is
    if ticker.isdigit() and len(ticker) == 6:
        return ticker
    
    # Direct match in mapping
    if ticker in STOCK_CODE_MAPPING:
        return STOCK_CODE_MAPPING[ticker]
    
    # Try case-insensitive match
    ticker_lower = ticker.lower()
    for name, code in STOCK_CODE_MAPPING.items():
        if name.lower() == ticker_lower:
            return code
    
    # Try partial match (contains)
    for name, code in STOCK_CODE_MAPPING.items():
        if ticker_lower in name.lower() or name.lower() in ticker_lower:
            return code
    
    # Try fuzzy match for common variations
    fuzzy_map: Dict[str, str] = {
        "茅台": "600519",
        "五粮": "000858",
        "平安": "601318",
        "招行": "600036",
        "兴业": "601166",
        "工行": "601398",
        "建行": "601939",
        "中行": "601988",
        "农行": "601288",
        "宁德": "300750",
        "比亚迪": "002594",
        "美的": "000333",
        "格力": "000651",
        "万科": "000002",
        "保利": "600048",
        "恒瑞": "600276",
        "中信": "600030",
        "中金": "601995",
        "华泰": "601688",
        "东财": "300059",
        "同花": "300033",
        "中芯": "688981",
        "隆基": "601012",
        "通威": "600438",
        "三峡": "600905",
        "海康": "002415",
        "京东方": "000725",
        "中际": "300308",
        "三安": "600703",
        "长电": "600584",
        "北方": "002371",
        "中微": "688012",
        "华虹": "688347",
        "中芯国": "688981",
    }
    
    for key, code in fuzzy_map.items():
        if key in ticker:
            return code
    
    # If still not found, try to extract 6-digit code from input
    code_match = re.search(r"\d{6}", ticker)
    if code_match:
        return code_match.group()
    
    logger.warning(f"Could not find stock code for ticker: {ticker}")
    return None


def _guess_board_id(stock_code: str) -> int:
    """
    Guess the board ID based on stock code.
    
    Shanghai stocks start with 6 (board_id=1)
    Shenzhen stocks start with 0 or 3 (board_id=0)
    
    Args:
        stock_code: 6-digit stock code
        
    Returns:
        Board ID (1 for Shanghai, 0 for Shenzhen)
    """
    if not stock_code or len(stock_code) < 1:
        return 0
        
    # Shanghai stocks typically start with 6
    if stock_code.startswith("6"):
        return 1
    
    # Shenzhen stocks start with 0, 3 (including ChiNext)
    return 0


def _analyze_sentiment(title: str, content: str = "") -> Tuple[int, int, str]:
    """
    Analyze sentiment of a post based on keywords.
    
    Args:
        title: Post title
        content: Post content (optional)
        
    Returns:
        Tuple of (bullish_count, bearish_count, sentiment_tag)
    """
    text = (title + " " + content).lower()
    
    bullish_count = 0
    bearish_count = 0
    
    for keyword in BULLISH_KEYWORDS:
        if keyword.lower() in text:
            bullish_count += 1
            
    for keyword in BEARISH_KEYWORDS:
        if keyword.lower() in text:
            bearish_count += 1
    
    # Determine sentiment
    if bullish_count > bearish_count:
        sentiment = "📈"
    elif bearish_count > bullish_count:
        sentiment = "📉"
    else:
        sentiment = "💬"
    
    return bullish_count, bearish_count, sentiment


def _format_number(num: int) -> str:
    """Format large numbers with K/M suffix."""
    if num >= 100000000:
        return f"{num / 100000000:.1f}B"
    elif num >= 100000:
        return f"{num / 10000:.1f}W"
    elif num >= 1000:
        return f"{num / 1000:.1f}K"
    return str(num)


def _truncate_text(text: str, max_length: int = 50) -> str:
    """Truncate text to max length with ellipsis."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_length:
        return text
    return text[:max_length] + "..."


# =============================================================================
# Main Function
# =============================================================================

def fetch_eastmoney_guba_sentiment(
    ticker: str,
    stock_code: Optional[str] = None,
    limit: int = 50
) -> str:
    """
    Fetch and analyze sentiment from East Money Guba (东方财富股吧) for a given stock.
    
    Args:
        ticker: Stock name or ticker symbol
        stock_code: Optional explicit stock code (overrides ticker lookup)
        limit: Maximum number of posts to fetch and analyze
        
    Returns:
        Formatted string with sentiment analysis results
        
    Example output:
        ========================================
        东方财富股吧情绪分析: 贵州茅台 (600519)
        ========================================
        总帖子数: 50
        
        [📈 看涨信号] 买入(3) 加仓(2) 满仓(1) 涨停(0) 牛市(0) ...
        [📉 看跌信号] 卖出(5) 清仓(3) 止损(2) 跌停(1) ...
        
        -----
        
        帖子详情 (按热度排序):
        
        [📈] 2024-01-15 10:30
        【茅台真的要起飞了】...
        浏览: 125.3K | 回复: 2.1K
        
        [💬] 2024-01-15 09:15
        【说说我的看法】...
        浏览: 45.2K | 回复: 856
        
        ...
    """
    # Get stock code
    code = stock_code if stock_code else _get_stock_code_from_ticker(ticker)
    
    if not code:
        return f"""
========================================
东方财富股吧情绪分析: {ticker}
========================================
[错误] 无法识别该股票代码
请检查股票名称或提供正确的6位代码。
"""
    
    # Get stock name from mapping
    stock_name = ticker
    for name, c in STOCK_CODE_MAPPING.items():
        if c == code:
            stock_name = name
            break
    
    board_id = _guess_board_id(code)
    
    # Fetch posts
    posts: List[Dict[str, Any]] = []
    
    # Try search API first
    posts = _fetch_via_search_api(stock_name, code, limit)
    
    # Fall back to page scraping if search API returns no results
    if not posts:
        logger.info(f"Search API returned no results, falling back to page scraping")
        for page in range(1, min(4, (limit // 20) + 2)):
            page_posts = _fetch_via_page_scrape(code, board_id, page)
            if page_posts:
                posts.extend(page_posts)
            if len(posts) >= limit:
                break
    
    # Try alternative page formats
    if not posts:
        # Try different URL formats
        alt_urls = [
            f"https://guba.eastmoney.com/list,{code},f.html",
            f"https://guba.eastmoney.com/list,{code}.html",
            f"https://guba.eastmoney.com/list,{code},1.html",
        ]
        for url in alt_urls:
            response = _make_request(url)
            if response:
                try:
                    from bs4 import BeautifulSoup
                    page_posts = _parse_with_bs4(response, code)
                    if page_posts:
                        posts.extend(page_posts)
                except ImportError:
                    page_posts = _parse_guba_html_via_regex(response)
                    if page_posts:
                        posts.extend(page_posts)
                if posts:
                    break
    
    # Sort by click count
    posts = sorted(posts, key=lambda x: x.get("click", 0), reverse=True)
    
    # Limit results
    posts = posts[:limit]
    
    # Count sentiment keywords
    total_bullish = 0
    total_bearish = 0
    bullish_stats: Dict[str, int] = {kw: 0 for kw in BULLISH_KEYWORDS}
    bearish_stats: Dict[str, int] = {kw: 0 for kw in BEARISH_KEYWORDS}
    
    for post in posts:
        title = post.get("title", "")
        content = post.get("content", "")
        text = (title + " " + content).lower()
        
        bullish_count = 0
        bearish_count = 0
        
        for keyword in BULLISH_KEYWORDS:
            count = text.count(keyword.lower())
            if count > 0:
                bullish_stats[keyword] += count
                bullish_count += count
                
        for keyword in BEARISH_KEYWORDS:
            count = text.count(keyword.lower())
            if count > 0:
                bearish_stats[keyword] += count
                bearish_count += count
        
        total_bullish += bullish_count
        total_bearish += bearish_count
    
    # Build output
    lines: List[str] = []
    
    lines.append("=" * 50)
    lines.append(f"东方财富股吧情绪分析: {stock_name} ({code})")
    lines.append("=" * 50)
    lines.append(f"总帖子数: {len(posts)}")
    lines.append("")
    
    # Sentiment summary
    sentiment_ratio = "偏多" if total_bullish > total_bearish else "偏空" if total_bearish > total_bullish else "中性"
    lines.append(f"市场情绪: {sentiment_ratio}")
    lines.append(f"看涨信号总数: {total_bullish}")
    lines.append(f"看跌信号总数: {total_bearish}")
    lines.append("")
    
    # Top bullish keywords
    top_bullish = sorted([(k, v) for k, v in bullish_stats.items() if v > 0], key=lambda x: x[1], reverse=True)[:10]
    if top_bullish:
        bullish_str = " ".join([f"{k}({v})" for k, v in top_bullish])
        lines.append(f"[📈 看涨关键词] {bullish_str}")
    else:
        lines.append("[📈 看涨关键词] 无")
    
    # Top bearish keywords
    top_bearish = sorted([(k, v) for k, v in bearish_stats.items() if v > 0], key=lambda x: x[1], reverse=True)[:10]
    if top_bearish:
        bearish_str = " ".join([f"{k}({v})" for k, v in top_bearish])
        lines.append(f"[📉 看跌关键词] {bearish_str}")
    else:
        lines.append("[📉 看跌关键词] 无")
    
    lines.append("")
    lines.append("-" * 50)
    lines.append("")
    lines.append("帖子详情 (按热度排序):")
    lines.append("")
    
    # Post details
    for i, post in enumerate(posts[:min(limit, 30)], 1):
        title = post.get("title", "")
        content = post.get("content", "")
        time_str = post.get("time", "")
        click = post.get("click", 0)
        reply = post.get("reply_count", 0)
        
        # Analyze sentiment
        _, _, sentiment_tag = _analyze_sentiment(title, content)
        
        # Format time
        if not time_str:
            time_str = "未知时间"
        
        lines.append(f"[{sentiment_tag}] {time_str}")
        
        if title:
            # Clean and truncate title
            clean_title = _clean_html(title)
            lines.append(f"  {_truncate_text(clean_title, 60)}")
        
        if content:
            lines.append(f"  {_truncate_text(_clean_html(content), 80)}")
        
        # Format counts
        click_str = _format_number(click) if click else "0"
        reply_str = _format_number(reply) if reply else "0"
        lines.append(f"  浏览: {click_str} | 回复: {reply_str}")
        lines.append("")
    
    # Final summary
    if posts:
        total_clicks = sum(p.get("click", 0) for p in posts)
        total_replies = sum(p.get("reply_count", 0) for p in posts)
        lines.append("-" * 50)
        lines.append(f"汇总统计:")
        lines.append(f"  总浏览量: {_format_number(total_clicks)}")
        lines.append(f"  总回复量: {_format_number(total_replies)}")
        lines.append(f"  平均热度: {_format_number(total_clicks // len(posts) if posts else 0)}")
    
    lines.append("")
    lines.append("=" * 50)
    lines.append(f"数据来源: 东方财富股吧 (guba.eastmoney.com)")
    lines.append(f"股票代码: {code} (board_id={board_id})")
    lines.append("=" * 50)
    
    return "\n".join(lines)


# =============================================================================
# Module Entry Point
# =============================================================================

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Example usage
    print(fetch_eastmoney_guba_sentiment("贵州茅台", limit=20))
