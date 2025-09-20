"""
odt_writer: Runs → ODT schreiben mit benannten Style-IDs
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any
import os
from urllib.parse import quote

from odf.opendocument import OpenDocumentText
from odf.text import P, Span, LineBreak, H, A, TableOfContent, TableOfContentSource, IndexBody, IndexTitle, PageNumber, PageCount
import re
from odf.draw import Frame, Image as DrawImage

from PIL import Image as PILImage  # nur für Dimensionen, optional
from odf.style import Style, TextProperties, ParagraphProperties, GraphicProperties, PageLayout, PageLayoutProperties, MasterPage, Footer

from .runs import RunsRecord, TextRun, EmojiRun, LineBreak as LB, ImageRun


def _ensure_min_styles(doc: OpenDocumentText, style_ids: Dict[str, Any]) -> Dict[str, Any]:
    """
    Legt minimale Styles mit den gewünschten Namen an, falls noch nicht vorhanden.
    style_ids erwartet keys: paragraph.base, text.base, graphic.inline_emoji
    Rückgabe enthält sowohl Namen als auch Style-Objekte (..Obj).
    """
    out = {
        "P.Base": style_ids.get("paragraph", {}).get("base", "P.Base"),
        "T.Base": style_ids.get("text", {}).get("base", "T.Base"),
        "G.InlineEmoji": style_ids.get("graphic", {}).get("inline_emoji", "G.InlineEmoji"),
    }

    p = Style(name=out["P.Base"], family="paragraph")
    p.addElement(ParagraphProperties(marginbottom="0.3cm", lineheight="150%"))
    doc.styles.addElement(p)

    t = Style(name=out["T.Base"], family="text")
    t.addElement(TextProperties())
    doc.styles.addElement(t)

    # Zusätzliche Textstile
    t_bold = Style(name="T.Bold", family="text"); t_bold.addElement(TextProperties(fontweight="bold")); doc.styles.addElement(t_bold)
    t_italic = Style(name="T.Italic", family="text"); t_italic.addElement(TextProperties(fontstyle="italic")); doc.styles.addElement(t_italic)
    t_underline = Style(name="T.Underline", family="text"); t_underline.addElement(TextProperties(textunderlinestyle="solid", textunderlinewidth="auto", textunderlinecolor="font-color")); doc.styles.addElement(t_underline)
    t_strike = Style(name="T.Strike", family="text"); t_strike.addElement(TextProperties(textlinethroughstyle="solid", textlinethroughwidth="auto", textlinethroughcolor="font-color")); doc.styles.addElement(t_strike)
    t_code = Style(name="T.Code", family="text"); t_code.addElement(TextProperties(fontname="Courier New")); doc.styles.addElement(t_code)
    t_spoiler = Style(name="T.Spoiler", family="text"); t_spoiler.addElement(TextProperties(color="#ffffff", backgroundcolor="#000000")); doc.styles.addElement(t_spoiler)

    g = Style(name=out["G.InlineEmoji"], family="graphic")
    # Minimaler Grafikstil ohne weitere Properties für maximale Kompatibilität
    doc.automaticstyles.addElement(g)

    out["P.BaseObj"] = p
    out["T.BaseObj"] = t
    out["G.InlineEmojiObj"] = g

    # Namen der Textstile mappen
    out["T.Bold"] = "T.Bold"; out["T.Italic"] = "T.Italic"; out["T.Underline"] = "T.Underline"; out["T.Strike"] = "T.Strike"; out["T.Code"] = "T.Code"; out["T.Spoiler"] = "T.Spoiler"

    return out


essential_images_dir = Path("cache/emoji")

def _add_emoji_as_char(doc: OpenDocumentText, para: P, doc_id: str, g_style_obj: Style, width_cm: float | None = None, height_cm: float | None = None):
    pic_name = f"{doc_id}.png"
    pic_path = essential_images_dir / pic_name
    if not pic_path.exists():
        # Fallback als Text, wenn PNG (noch) nicht vorhanden
        para.addElement(Span(text=f"[CE:{doc_id}]"))
        return
    # Im ODT referenzieren
    # addPicture kopiert hinein und liefert die interne HREF zurück
    rel_href = doc.addPicture(str(pic_path))
    # Standardgröße für Emoji, falls nichts angegeben: 0.6cm
    if height_cm is None and width_cm is None:
        height_cm = 0.6
        width_cm = 0.6
    # Frame mit Größenangaben direkt setzen
    kwargs = {"stylename": g_style_obj, "anchortype": "as-char"}
    if width_cm is not None:
        kwargs["width"] = f"{width_cm}cm"
    if height_cm is not None:
        kwargs["height"] = f"{height_cm}cm"
    frame = Frame(**kwargs)
    img = DrawImage(href=rel_href, type="simple", show="embed", actuate="onLoad")
    frame.addElement(img)
    para.addElement(frame)


def _add_image_block(doc: OpenDocumentText, img_path: Path, p: P, g_style_obj: Style, width_cm: float = 15.0) -> None:
    if not img_path.exists():
        p.addElement(Span(text=f"[IMG missing: {img_path.name}]"))
        return
    # Referenzname im ODT (von odfpy generiert)
    rel_href = doc.addPicture(str(img_path))
    # Höhe proportional zur Bildgröße festlegen
    height_cm = None
    try:
        with PILImage.open(img_path) as im:
            w, h = im.size
            if w > 0 and h > 0:
                height_cm = width_cm * (h / w)
    except Exception:
        pass
    # Frame mit Breite/Höhe – as-char verankert, mit Mindesthöhe 6.0cm
    min_height_cm = 6.0
    height_cm_calc = None
    try:
        with PILImage.open(img_path) as im:
            w, h = im.size
            if w > 0 and h > 0:
                height_cm_calc = width_cm * (h / w)
    except Exception:
        pass
    if height_cm_calc is None:
        height_cm_calc = min_height_cm
    if height_cm_calc < min_height_cm:
        height_cm_calc = min_height_cm
    frame = Frame(stylename=g_style_obj, width=f"{width_cm}cm", height=f"{height_cm_calc:.3f}cm", anchortype="as-char")
    frame.addElement(DrawImage(href=rel_href, type="simple", show="embed", actuate="onLoad"))
    p.addElement(frame)


def _sanitize_text(s: str) -> str:
    # Entferne nicht erlaubte XML-Kontrollzeichen (0x00-0x08,0x0B,0x0C,0x0E-0x1F)
    return re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", " ", s or "")


def _add_footer(doc, styles_map):
    pl = PageLayout(name="pm1")
    pl.addElement(PageLayoutProperties(pagewidth="21cm", pageheight="29.7cm",
                                       margintop="1.5cm", marginbottom="1.5cm",
                                       marginleft="2cm", marginright="2cm"))
    doc.automaticstyles.addElement(pl)
    mp = MasterPage(name="Standard", pagelayoutname=pl)
    f = Footer()
    # Footer paragraph style: centered, font size 8pt
    foot_style = Style(name="FooterPara", family="paragraph")
    foot_style.addElement(ParagraphProperties(textalign="center"))
    foot_text = Style(name="FooterText", family="text")
    foot_text.addElement(TextProperties(fontsize="8pt"))
    doc.styles.addElement(foot_style)
    doc.styles.addElement(foot_text)
    p = P(stylename=foot_style)
    s1 = Span(stylename=foot_text)
    s1.addElement(PageNumber(selectpage="current"))
    p.addElement(s1)
    p.addElement(Span(text=" / ", stylename=foot_text))
    s2 = Span(stylename=foot_text)
    s2.addElement(PageCount())
    p.addElement(s2)
    f.addElement(p); mp.addElement(f); doc.masterstyles.addElement(mp)


def _add_toc(doc):
    toc = TableOfContent(name="ToC", protected="true")
    src = TableOfContentSource(outlinelevel=10, indexscope="document")
    toc.addElement(src)
    body = IndexBody(); it = IndexTitle(name="ToCTitle"); it.addElement(P(text="Inhaltsverzeichnis")); body.addElement(it)
    toc.addElement(body); doc.text.addElement(toc)


def write_odt_for_records(records: List[RunsRecord], out_path: Path, styles: Dict[str, Any], doc_title: str | None = None) -> Path:
    doc = OpenDocumentText()
    style_names = _ensure_min_styles(doc, styles or {})
    # Dokumenttitel (optional)
    if doc_title:
        tstyle = Style(name="TitlePara", family="paragraph")
        tstyle.addElement(ParagraphProperties(textalign="center"))
        tstyle_text = Style(name="TitleText", family="text")
        tstyle_text.addElement(TextProperties(fontsize="16pt", fontweight="bold"))
        doc.styles.addElement(tstyle)
        doc.styles.addElement(tstyle_text)
        tp = P(stylename=tstyle)
        tp.addElement(Span(text=str(doc_title), stylename=tstyle_text))
        doc.text.addElement(tp)
    # TOC + Footer wie im Originalskript
    _add_toc(doc)
    _add_footer(doc, style_names)

    # Einfache Struktur: H1 für Gruppe/Chat, danach Runs je Nachricht als Absätze
    current_chat = None
    for rec in records:
        if rec.chat != current_chat:
            current_chat = rec.chat
            doc.text.addElement(H(outlinelevel=1, text=_sanitize_text(str(current_chat))))
        # Jede Nachricht als Absatzblock (nutze Defaultstil)
        p = P()
        for r in rec.runs:
            if isinstance(r, ImageRun):
                # Bild in eigenem Absatz (ohne zusätzliche Leerzeilen)
                p_img = P()
                _add_image_block(doc, Path(r.path), p_img, style_names["G.InlineEmojiObj"], width_cm=r.width_cm)
                doc.text.addElement(p_img)
            elif isinstance(r, TextRun):
                # TextRun → Text mit expliziten LineBreaks, Styles und optionalem Link
                parts = (r.text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
                for idx, seg in enumerate(parts):
                    if seg:
                        parent = p
                        if r.href:
                            a = A(href=r.href)
                            parent.addElement(a)
                            parent = a
                        # verschachtelte Spans für Styles
                        container = parent
                        for flag, sty_key in ((r.bold, "T.Bold"),(r.italic, "T.Italic"),(r.underline, "T.Underline"),(r.strike, "T.Strike"),(r.code, "T.Code"),(r.spoiler, "T.Spoiler")):
                            if flag:
                                sp = Span(stylename=style_names.get(sty_key))
                                container.addElement(sp)
                                container = sp
                        container.addElement(Span(text=_sanitize_text(seg)))
                    if idx < len(parts) - 1:
                        p.addElement(LineBreak())
            elif isinstance(r, LB):
                p.addElement(LineBreak())
            elif isinstance(r, EmojiRun):
                # Emoji als inlined frame (hier Style-Objekt notwendig)
                _add_emoji_as_char(doc, p, r.document_id, style_names["G.InlineEmojiObj"])
        doc.text.addElement(p)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path

