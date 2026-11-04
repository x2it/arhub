#!/usr/bin/env python3
"""
生成模拟「纸质档案扫描件」。

关键点：产出的是纯图片型 PDF（不含任何文本层），
只有经过 OCR 才能提取文字 —— 这样才能真实验证端到端链路。
同时叠加真实扫描件常见的退化：轻微歪斜、噪点、灰度化、亮度不均。
"""
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")

# A4 @ 200dpi
PAGE_W, PAGE_H = 1654, 2339
DPI = 200


def load_font(size, bold=False):
    for path in FONT_CANDIDATES:
        if not os.path.exists(path):
            continue
        for idx in range(3):  # .ttc 里有多张 face，逐个试
            try:
                f = ImageFont.truetype(path, size, index=idx)
                # 用中文字符验证这张 face 真的有字形
                if f.getbbox("档")[2] > 0:
                    return f
            except Exception:
                continue
    raise RuntimeError("未找到可用的中文字体")


# 8 份档案，主题刻意错开，便于验证语义检索的区分度
ARCHIVES = [
    {
        "file": "设备采购合同.pdf",
        "title": "2023年度实验室设备采购合同",
        "correspondent": "采购部",
        "doc_type": "合同",
        "tags": ["合同", "采购"],
        "created": "2023-03-15",
        "asn": 1001,
        "body": [
            "甲方：某某研究院",
            "乙方：某某科学仪器有限公司",
            "",
            "第一条 采购标的",
            "甲方向乙方采购高精度离心机两台、恒温培养箱三台，",
            "合计金额为人民币肆拾贰万元整。",
            "",
            "第二条 交付与验收",
            "乙方应于合同签订之日起六十日内完成全部设备交付，",
            "甲方在收到设备后十五个工作日内组织验收，",
            "验收合格后方可办理付款手续。",
            "",
            "第三条 质量保证",
            "设备质保期为三年，质保期内因产品质量问题",
            "导致的故障，乙方负责免费维修或更换。",
        ],
    },
    {
        "file": "安全生产会议纪要.pdf",
        "title": "2023年第四季度安全生产工作会议纪要",
        "correspondent": "安全生产委员会",
        "doc_type": "会议纪要",
        "tags": ["会议", "安全生产"],
        "created": "2023-11-08",
        "asn": 1002,
        "body": [
            "会议时间：2023年11月8日 下午两点",
            "会议地点：行政楼三楼会议室",
            "主持人：张建国",
            "",
            "一、上季度安全形势通报",
            "上季度全院未发生重大安全生产事故，",
            "但实验室废弃物处置环节仍存在两处隐患，",
            "责任部门须在本月底前完成整改。",
            "",
            "二、消防设施检查情况",
            "经排查，三号实验楼灭火器有十二具已过有效期，",
            "需立即更换；消防通道堆放杂物问题已现场清理。",
            "",
            "三、下一步工作安排",
            "各部门须于十二月中旬前组织一次安全演练，",
            "并将演练记录报送安全委员会备案。",
        ],
    },
    {
        "file": "住房公积金调整通知.pdf",
        "title": "关于调整职工住房公积金缴存比例的通知",
        "correspondent": "人力资源部",
        "doc_type": "通知",
        "tags": ["人事", "福利"],
        "created": "2024-01-20",
        "asn": 1003,
        "body": [
            "各部门、各直属单位：",
            "",
            "经研究决定，自2024年2月1日起，",
            "将职工住房公积金缴存比例由百分之八调整至百分之十，",
            "单位配套缴存比例同步调整。",
            "",
            "本次调整适用于全体在编职工及劳动合同制人员。",
            "个人缴存部分由财务部门在当月工资中代扣代缴。",
            "",
            "如对缴存基数有异议，请于1月31日前",
            "与人力资源部薪酬科联系核实。",
            "",
            "特此通知。",
        ],
    },
    {
        "file": "差旅费报销规定.pdf",
        "title": "工作人员差旅费报销标准暂行规定",
        "correspondent": "财务部",
        "doc_type": "规定",
        "tags": ["财务", "报销"],
        "created": "2024-04-02",
        "asn": 1004,
        "body": [
            "第一章 总则",
            "为规范差旅费管理，节约开支，制定本规定。",
            "",
            "第二章 城市间交通费",
            "工作人员出差乘坐火车硬席、轮船三等舱、",
            "飞机经济舱的，凭据报销。",
            "乘坐高铁一等座需事先经分管领导批准。",
            "",
            "第三章 住宿费",
            "住宿费标准按出差目的地城市分档执行，",
            "一线城市每晚不超过五百元，其他城市不超过三百五十元。",
            "",
            "第四章 报销时限",
            "出差人员应在返回后三十日内办理报销手续，",
            "逾期未办理的，须书面说明理由。",
        ],
    },
    {
        "file": "仪器维修记录.pdf",
        "title": "2024年上半年仪器设备维修记录台账",
        "correspondent": "设备管理科",
        "doc_type": "台账",
        "tags": ["设备", "维修"],
        "created": "2024-06-30",
        "asn": 1005,
        "body": [
            "序号  设备名称      故障现象          维修日期",
            "001   气相色谱仪    基线漂移          2024-02-14",
            "002   恒温培养箱    温控失灵          2024-03-22",
            "003   电子天平      读数不稳定        2024-04-11",
            "004   离心机        异响              2024-05-09",
            "005   超纯水机      产水电阻率下降    2024-06-18",
            "",
            "维修费用合计：人民币叁万柒仟贰佰元整。",
            "其中气相色谱仪更换检测器一支，费用最高。",
            "",
            "下半年计划对使用年限超过八年的设备",
            "统一组织技术鉴定，决定是否报废更新。",
        ],
    },
    {
        "file": "消防安全责任书.pdf",
        "title": "2024年度消防安全目标责任书",
        "correspondent": "保卫处",
        "doc_type": "责任书",
        "tags": ["安全", "消防"],
        "created": "2024-01-05",
        "asn": 1006,
        "body": [
            "为落实消防安全责任制，预防和减少火灾危害，",
            "特签订本责任书。",
            "",
            "一、责任目标",
            "全年不发生较大以上火灾事故，",
            "一般火灾事故起数较上年下降。",
            "",
            "二、责任内容",
            "（一）落实逐级消防安全责任制，明确各岗位责任人；",
            "（二）每月组织一次防火检查，及时消除火灾隐患；",
            "（三）保障疏散通道、安全出口、消防车通道畅通；",
            "（四）定期维护保养消防设施器材，确保完好有效。",
            "",
            "三、考核与奖惩",
            "本责任书执行情况纳入年度考核，",
            "对失职造成火灾事故的，依法依规追究责任。",
        ],
    },
    {
        "file": "财务审计报告.pdf",
        "title": "2023年度财务收支审计报告",
        "correspondent": "审计处",
        "doc_type": "审计报告",
        "tags": ["财务", "审计"],
        "created": "2024-05-16",
        "asn": 1007,
        "body": [
            "根据年度审计计划，审计处对2023年度",
            "财务收支情况进行了审计，现将结果报告如下。",
            "",
            "一、基本情况",
            "2023年度收入总额为人民币壹亿贰仟叁佰万元，",
            "支出总额为人民币壹亿壹仟捌佰万元，",
            "当年结余伍佰万元。",
            "",
            "二、审计发现的问题",
            "（一）部分差旅费报销凭证附件不完整；",
            "（二）两台已提足折旧的设备未及时办理报废手续；",
            "（三）个别采购事项未严格执行比价程序。",
            "",
            "三、审计意见",
            "上述问题不影响财务报表的整体公允性，",
            "建议相关部门在三个月内完成整改。",
        ],
    },
    {
        "file": "人事任免通知.pdf",
        "title": "关于李明等同志职务任免的通知",
        "correspondent": "人力资源部",
        "doc_type": "通知",
        "tags": ["人事", "任免"],
        "created": "2024-09-10",
        "asn": 1008,
        "body": [
            "经院党组会议研究决定：",
            "",
            "任命李明同志为科技管理处副处长，",
            "免去其成果转化办公室主任职务；",
            "",
            "任命王芳同志为国际合作处副处长，",
            "试用期一年；",
            "",
            "免去张伟同志后勤服务中心主任职务，",
            "另有任用。",
            "",
            "上述人员职务变动自2024年9月1日起生效。",
            "请有关部门按规定办理工作交接手续。",
        ],
    },
]


def render_page(archive):
    """把一份档案渲染成一页 A4 位图，并叠加扫描退化效果。"""
    img = Image.new("L", (PAGE_W, PAGE_H), 255)
    draw = ImageDraw.Draw(img)

    title_font = load_font(46)
    body_font = load_font(30)
    foot_font = load_font(24)

    margin = 150
    y = 170

    # 标题居中
    bbox = draw.textbbox((0, 0), archive["title"], font=title_font)
    draw.text(((PAGE_W - (bbox[2] - bbox[0])) / 2, y), archive["title"], font=title_font, fill=15)
    y += 110

    # 标题下的分隔线
    draw.line([(margin, y), (PAGE_W - margin, y)], fill=60, width=3)
    y += 70

    for line in archive["body"]:
        if y > PAGE_H - 220:
            break
        draw.text((margin, y), line, font=body_font, fill=25)
        y += 52

    # 页脚：档号 + 日期，模拟实体档案的盖章/编号区
    foot = f"档号：DA-{archive['asn']}    形成日期：{archive['created']}"
    draw.text((margin, PAGE_H - 170), foot, font=foot_font, fill=70)

    # ---- 叠加扫描退化效果 ----
    # 1. 轻微歪斜（真实扫描件几乎不可能是 0 度）
    angle = random.uniform(-0.9, 0.9)
    img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=255, expand=False)

    # 2. 轻微模糊（扫描仪/镜头失焦）
    img = img.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.3, 0.7)))

    # 3. 噪点
    px = img.load()
    for _ in range(int(PAGE_W * PAGE_H * 0.002)):
        x, y = random.randrange(PAGE_W), random.randrange(PAGE_H)
        v = px[x, y]
        px[x, y] = max(0, min(255, v + random.randint(-28, 18)))

    return img


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    random.seed(42)  # 固定随机种子，保证可复现

    for a in ARCHIVES:
        img = render_page(a)
        path = os.path.join(OUT_DIR, a["file"])
        img.convert("RGB").save(path, "PDF", resolution=float(DPI))
        print(f"[生成] {a['file']}  档号 DA-{a['asn']}  {a['title']}")

    print(f"\n共 {len(ARCHIVES)} 份模拟扫描件 -> {OUT_DIR}")
    print("注意：这些 PDF 不含文本层，必须经 OCR 才能检索。")


if __name__ == "__main__":
    main()
