"""
odt_writer: Runs → ODT schreiben mit benannten Style-IDs
"""
from __future__ import annotations
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
import itertools
import os
from urllib.parse import quote

from odf.opendocument import OpenDocumentText
from odf.text import P, Span, LineBreak, H, A, Bookmark, TableOfContent, TableOfContentSource, IndexBody, IndexTitle, PageNumber, PageCount
import re
from odf.draw import Frame, Image as DrawImage
from odf.table import Table, TableColumn, TableRow, TableCell

from PIL import Image as PILImage  # nur für Dimensionen, optional
from odf.style import (
    Style, TextProperties, ParagraphProperties, GraphicProperties, PageLayout, PageLayoutProperties,
    MasterPage, Footer, TableProperties, TableColumnProperties, TableCellProperties,
)

from .runs import RunsRecord, RecordPair, TextRun, EmojiRun, LineBreak as LB, ImageRun


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

    # Namen/Attribute folgen der ODF-Konvention für native Gliederungsstile
    # ("Heading_20_1" = interner Name für Anzeigename "Heading 1", "_20_"
    # kodiert das Leerzeichen). style:default-outline-level ist die
    # eigentliche Verknüpfung Style<->Gliederungsebene - Word/LibreOffice
    # erkennen Absätze mit diesen Styles dadurch unabhängig von unserer
    # eigenen TOC-Erzeugung als Gliederungsebene in Navigator/Navigationsleiste
    # (das reine text:outline-level am <text:h>-Element allein reicht dafür
    # nicht überall, insb. nicht für Word-Kompatibilität).
    h1 = Style(name="Heading_20_1", family="paragraph", displayname="Heading 1", defaultoutlinelevel="1")
    h1.addElement(ParagraphProperties(marginbottom="0.2cm"))
    h1.addElement(TextProperties(fontsize="14pt", fontweight="bold"))
    doc.styles.addElement(h1)
    out["H.Base"] = "Heading_20_1"

    # Seitenumbruch-Variante von Heading 1 (ab der zweiten Section) - eigener
    # Style-Name, da style:name je Familie eindeutig sein muss, aber über
    # parent-style-name + eigenes default-outline-level weiterhin klar als
    # Heading-1-Variante erkennbar.
    h1_break = Style(
        name="Heading_20_1_20_Break", family="paragraph",
        displayname="Heading 1 (Seitenumbruch)", parentstylename="Heading_20_1",
        defaultoutlinelevel="1",
    )
    h1_break.addElement(ParagraphProperties(marginbottom="0.2cm", breakbefore="page"))
    h1_break.addElement(TextProperties(fontsize="14pt", fontweight="bold"))
    doc.styles.addElement(h1_break)
    out["H.Break"] = "Heading_20_1_20_Break"

    h2 = Style(name="Heading_20_2", family="paragraph", displayname="Heading 2", defaultoutlinelevel="2")
    h2.addElement(ParagraphProperties(marginbottom="0.15cm"))
    h2.addElement(TextProperties(fontsize="12pt", fontweight="bold"))
    doc.styles.addElement(h2)
    out["H.Sub"] = "Heading_20_2"

    toc1 = Style(name="TOC.Lvl1", family="paragraph")
    toc1.addElement(ParagraphProperties(marginbottom="0.1cm"))
    doc.styles.addElement(toc1)
    out["TOC.Lvl1"] = "TOC.Lvl1"

    g = Style(name=out["G.InlineEmoji"], family="graphic")
    # Minimaler Grafikstil ohne weitere Properties für maximale Kompatibilität
    doc.automaticstyles.addElement(g)

    out["P.BaseObj"] = p
    out["T.BaseObj"] = t
    out["G.InlineEmojiObj"] = g

    # Namen der Textstile mappen
    out["T.Bold"] = "T.Bold"; out["T.Italic"] = "T.Italic"; out["T.Underline"] = "T.Underline"; out["T.Strike"] = "T.Strike"; out["T.Code"] = "T.Code"; out["T.Spoiler"] = "T.Spoiler"

    link_para = Style(name="P.MessageLink", family="paragraph")
    link_para.addElement(ParagraphProperties(marginbottom="0.2cm"))
    doc.styles.addElement(link_para)
    out["P.MessageLink"] = "P.MessageLink"

    header_para = Style(name="P.MessageHeader", family="paragraph")
    header_para.addElement(ParagraphProperties(margintop="0.1cm", marginbottom="0.2cm", backgroundcolor="#f2f2f2", paddingtop="0.05cm", paddingbottom="0.05cm"))
    doc.styles.addElement(header_para)
    out["P.MessageHeader"] = "P.MessageHeader"

    pb = Style(name="P.PageBreak", family="paragraph")
    pb.addElement(ParagraphProperties(breakbefore="page"))
    doc.automaticstyles.addElement(pb)
    out["P.PageBreak"] = "P.PageBreak"

    separator = Style(name="P.MessageSeparator", family="paragraph")
    separator.addElement(ParagraphProperties(borderbottom="0.02cm solid #000000", marginbottom="0.35cm", margintop="0.35cm"))
    doc.styles.addElement(separator)
    out["P.MessageSeparator"] = "P.MessageSeparator"

    return out


# Seitenbreite abzüglich Rand (siehe _add_footer: 21cm Seite, je 2cm Rand
# links/rechts) - nutzbare Breite für das side_by_side-Tabellenlayout.
_PAGE_USABLE_WIDTH_CM = 17.0

# side_by_side-Dokumente werden im Querformat geschrieben (siehe _add_footer),
# damit die zwei Spalten (Original/Übersetzung) nicht auf Hochformat-Breite
# gequetscht werden: 29,7cm Seitenbreite abzüglich je 2cm Rand links/rechts.
_PAGE_USABLE_WIDTH_LANDSCAPE_CM = 25.7


def _ensure_table_styles(doc: OpenDocumentText, style_names: Dict[str, Any], usable_width_cm: float = _PAGE_USABLE_WIDTH_CM) -> Dict[str, Any]:
    """Legt Styles für das side_by_side-Tabellenlayout an (zwei gleich breite
    Spalten). may-break-between-rows bleibt bewusst aktiv (Default), damit
    lange Nachrichten die Zeile über eine Seitengrenze hinweg umbrechen
    können, statt entweder abgeschnitten zu werden oder die Seite zu sprengen
    - siehe Risiko "Seitenumbrüche in langen Zellen" in der Aufgabenstellung."""
    col_width_cm = usable_width_cm / 2

    table_style = Style(name="Table.SideBySide", family="table")
    table_style.addElement(TableProperties(width=f"{usable_width_cm}cm", align="margins"))
    doc.automaticstyles.addElement(table_style)
    style_names["Table.SideBySide"] = "Table.SideBySide"

    col_style = Style(name="TCol.Half", family="table-column")
    col_style.addElement(TableColumnProperties(columnwidth=f"{col_width_cm}cm"))
    doc.automaticstyles.addElement(col_style)
    style_names["TCol.Half"] = "TCol.Half"
    style_names["_side_by_side_col_width_cm"] = col_width_cm

    cell_style = Style(name="TCell.Base", family="table-cell")
    cell_style.addElement(TableCellProperties(padding="0.15cm", borderbottom="0.02cm solid #000000", verticalalign="top"))
    doc.automaticstyles.addElement(cell_style)
    style_names["TCell.Base"] = "TCell.Base"

    header_cell_style = Style(name="TCell.ColumnHeader", family="table-cell")
    header_cell_style.addElement(TableCellProperties(padding="0.15cm", backgroundcolor="#e0e0e0", borderbottom="0.02cm solid #000000", verticalalign="top"))
    doc.automaticstyles.addElement(header_cell_style)
    style_names["TCell.ColumnHeader"] = "TCell.ColumnHeader"

    col_header_text = Style(name="T.ColumnHeader", family="text")
    col_header_text.addElement(TextProperties(fontweight="bold"))
    doc.styles.addElement(col_header_text)
    style_names["T.ColumnHeader"] = "T.ColumnHeader"

    # Eigener (kleinerer) Absatzstil für den Nachrichtentext in den Tabellen-
    # zellen - Schriftgröße lässt sich in ODF nur über den Absatz-/Textstil
    # setzen, nicht über TCell.Base (table-cell-Styles beeinflussen nur
    # Rahmen/Innenabstand/Hintergrund, keine Textformatierung). P.Base selbst
    # bleibt für den linearen Fließtext unverändert.
    cell_para_style = Style(name="P.CellBase", family="paragraph")
    cell_para_style.addElement(ParagraphProperties(marginbottom="0.3cm", lineheight="150%"))
    cell_para_style.addElement(TextProperties(fontsize="10pt"))
    doc.automaticstyles.addElement(cell_para_style)
    style_names["P.CellBase"] = "P.CellBase"

    return style_names


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


def _render_run_into_paragraph(doc: OpenDocumentText, p: P, r: Any, style_names: Dict[str, Any]) -> None:
    """Rendert einen einzelnen Run (TextRun/LineBreak/EmojiRun) in den
    bestehenden Absatz `p`. Kernstück der vormals doppelt vorhandenen
    Rendering-Logik (Nachrichtenkopf und Nachrichtentext nutzten fast
    identischen Code). ImageRun wird hier bewusst NICHT behandelt - Bilder
    brauchen einen eigenen Absatz, siehe render_runs_into_container()."""
    if isinstance(r, TextRun):
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
                for flag, sty_key in (
                    (r.bold, "T.Bold"), (r.italic, "T.Italic"), (r.underline, "T.Underline"),
                    (r.strike, "T.Strike"), (r.code, "T.Code"), (r.spoiler, "T.Spoiler"),
                ):
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
        _add_emoji_as_char(doc, p, r.document_id, style_names["G.InlineEmojiObj"])
    # ImageRun: bewusst kein Fall hier, siehe Docstring.


def render_runs_into_container(
    doc: OpenDocumentText,
    container: Any,
    runs: List[Any],
    style_names: Dict[str, Any],
    base_para_style: str,
    max_image_width_cm: Optional[float] = None,
) -> None:
    """Rendert eine vollständige Run-Liste (Nachrichtentext) als Absätze in
    `container` - das kann doc.text (linear, bisheriges Verhalten) oder eine
    Tabellenzelle sein (side_by_side-Layout, siehe write_odt_for_record_pairs).
    Bilder (ImageRun) bekommen einen eigenen Absatz, unabhängig vom
    umgebenden Text-Absatz; dessen Erzeugen/Einhängen entspricht exakt dem
    vormaligen Verhalten (ein einziger Text-Absatz, am Ende eingehängt -
    auch wenn er leer ist, wie zuvor).

    max_image_width_cm: falls gesetzt, wird die (im Run fest hinterlegte)
    Bildbreite auf diesen Wert gedeckelt - nötig im side_by_side-Layout,
    wo die feste Standardbreite (10cm) nicht in eine ca. 8cm schmale
    Tabellenspalte passt. Ohne Angabe (linear) unverändertes Verhalten."""
    p = P(stylename=base_para_style)
    for r in runs:
        if isinstance(r, ImageRun):
            p_img = P(stylename=base_para_style)
            width_cm = r.width_cm
            if max_image_width_cm is not None and width_cm > max_image_width_cm:
                width_cm = max_image_width_cm
            _add_image_block(doc, Path(r.path), p_img, style_names["G.InlineEmojiObj"], width_cm=width_cm)
            container.addElement(p_img)
        else:
            _render_run_into_paragraph(doc, p, r, style_names)
    container.addElement(p)


def _build_header_paragraph(
    doc: OpenDocumentText,
    header_runs: List[Any] | None,
    link_text: str | None,
    style_names: Dict[str, Any],
) -> P | None:
    """Baut den Nachrichtenkopf-Absatz (Zeitstempel/Link/Autor). Gibt None
    zurück, wenn nichts zu rendern ist - der Aufrufer hängt das Ergebnis
    dann selbst in den gewünschten Container ein (doc.text oder Zelle)."""
    if not header_runs and not link_text:
        return None
    p_header = P(stylename=style_names.get("P.MessageHeader", style_names.get("P.Base")))
    for r in header_runs or []:
        _render_run_into_paragraph(doc, p_header, r, style_names)
    # Prüfen, ob der Permalink bereits als Link-Run im Header vorkam (dann
    # keinen zweiten, redundanten Link anhängen). Reine Eingabe-Prüfung,
    # unabhängig vom eigentlichen Rendern.
    link_inserted_header = any(
        isinstance(r, TextRun) and r.href and link_text and r.href == link_text
        for r in (header_runs or [])
    )
    if link_text and not link_inserted_header:
        a = A(href=link_text)
        p_header.addElement(a)
        container = Span(stylename=style_names.get("T.Bold"))
        a.addElement(container)
        underline_span = Span(stylename=style_names.get("T.Underline"))
        container.addElement(underline_span)
        underline_span.addElement(Span(text=_sanitize_text(link_text)))
    return p_header


def _add_footer(doc, styles_map, landscape: bool = False):
    pl = PageLayout(name="pm1")
    page_width, page_height = ("29.7cm", "21cm") if landscape else ("21cm", "29.7cm")
    pl.addElement(PageLayoutProperties(pagewidth=page_width, pageheight=page_height,
                                       printorientation="landscape" if landscape else "portrait",
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


def _add_toc(doc, styles_map) -> IndexBody:
    """Schreibt das TOC-Skelett und gibt die IndexBody zurück, damit der
    Aufrufer sie nach dem Hauptinhalt mit echten Einträgen füllen kann
    (siehe _populate_toc) - Text und outlinelevel der Überschriften liegen
    erst nach dem Schleifendurchlauf vollständig vor, die Body-Position im
    Dokument muss aber schon vorher (vor dem Inhalt) feststehen."""
    toc = TableOfContent(name="ToC", protected="true")
    # outlinelevel=1: nur Ebene-1-Überschriften (Section-Titel) landen im
    # Verzeichnis - wie in Word/LibreOffice-Standard-TOCs. Wirkt sich zwar
    # nur auf ein natives "Index aktualisieren" aus (unsere eigene
    # _populate_toc() unten liest das Attribut nicht), hält das Skelett aber
    # konsistent mit den tatsächlich eingetragenen Ebenen (siehe
    # _add_heading_with_bookmark: registriert ebenfalls nur Ebene 1).
    src = TableOfContentSource(outlinelevel=1, indexscope="document")
    toc.addElement(src)
    body = IndexBody(); it = IndexTitle(name="ToCTitle"); it.addElement(P(text="Inhaltsverzeichnis")); body.addElement(it)
    toc.addElement(body); doc.text.addElement(toc)
    pb_name = styles_map.get("P.PageBreak")
    if pb_name:
        doc.text.addElement(P(stylename=pb_name))
    return body


def _add_heading_with_bookmark(
    container: Any,
    level: int,
    text: str,
    stylename: Optional[str],
    bookmark_name: str,
    toc_entries: List[Tuple[str, int, str]],
) -> None:
    """Schreibt eine H()-Überschrift. Nur Ebene 1 (Section-Titel) bekommt ein
    eingebettetes Punkt-Bookmark als Sprungziel und wird für _populate_toc
    gesammelt - wie in Word/LibreOffice-Standard-TOCs landen tiefere Ebenen
    (z.B. die H2-Kanalmarker beim chronologischen Mischen) nicht im
    Inhaltsverzeichnis, bleiben im Dokument aber sichtbar. Text wird bewusst
    über addText() statt des text=-Kwargs gesetzt, damit das Bookmark als
    erstes Kind vor dem Textknoten liegt."""
    clean_text = _sanitize_text(text)
    h = H(outlinelevel=level, stylename=stylename)
    if level == 1:
        h.addElement(Bookmark(name=bookmark_name))
    h.addText(clean_text)
    container.addElement(h)
    if level == 1:
        toc_entries.append((clean_text, level, bookmark_name))


def _populate_toc(body: IndexBody, style_names: Dict[str, Any], entries: List[Tuple[str, int, str]]) -> None:
    """Füllt das per _add_toc angelegte Skelett mit echten Einträgen, sodass
    LibreOffice/Word das Verzeichnis bereits beim Öffnen befüllt zeigen -
    ohne manuelles 'Index aktualisieren'. Jeder Eintrag ist ein interner
    Link (href="#bookmark") auf das zugehörige, per
    _add_heading_with_bookmark gesetzte Bookmark. Auf ein Seitenzahl-Feld
    wird bewusst verzichtet: ohne echten Layout-Renderer lässt sich die
    tatsächliche Seite beim Schreiben nicht zuverlässig ermitteln - der
    klickbare Sprung zur Überschrift wiegt das auf."""
    for text, _level, bookmark_name in entries:
        # entries enthält ausschließlich Ebene-1-Einträge (siehe
        # _add_heading_with_bookmark), daher immer derselbe Absatzstil.
        p = P(stylename=style_names.get("TOC.Lvl1"))
        a = A(href=f"#{bookmark_name}")
        p.addElement(a)
        bold = Span(stylename=style_names.get("T.Bold"))
        a.addElement(bold)
        underline = Span(stylename=style_names.get("T.Underline"))
        bold.addElement(underline)
        underline.addElement(Span(text=text))
        body.addElement(p)


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
    toc_body = _add_toc(doc, style_names)
    _add_footer(doc, style_names)

    # Einfache Struktur: H1 für Gruppe/Chat, danach Runs je Nachricht als Absätze
    current_chat = None
    seen_subheading: Dict[str, bool] = {}
    seen_channel_labels: Dict[str, bool] = {}
    toc_entries: List[Tuple[str, int, str]] = []
    bookmark_counter = itertools.count(1)
    for rec in records:
        if rec.chat != current_chat:
            heading_style = style_names.get("H.Base") if current_chat is None else style_names.get("H.Break")
            _add_heading_with_bookmark(
                doc.text, 1, str(rec.chat), heading_style,
                f"toc_bm_{next(bookmark_counter)}", toc_entries,
            )
            current_chat = rec.chat
            if rec.meta and rec.meta.get("subheading") and not seen_subheading.get(rec.chat):
                _add_heading_with_bookmark(
                    doc.text, 2, str(rec.meta["subheading"]), style_names.get("H.Sub"),
                    f"toc_bm_{next(bookmark_counter)}", toc_entries,
                )
                seen_subheading[rec.chat] = True
        # channel_label (chronologisches Mischen, siehe runner_schedule.py):
        # rec.chat bleibt für alle Nachrichten gleich, daher hier - anders
        # als bei subheading oben - unabhängig vom chat-Wechsel geprüft. Das
        # "erste Auftreten" wird bewusst erst hier, beim tatsächlichen
        # Schreiben der finalen (ggf. aus dem Store neu sortierten) Liste
        # ermittelt statt beim Sammeln der Nachrichten - sonst würde ein
        # späterer inkrementeller Lauf (frischer, leerer Sichtbarkeits-Stand)
        # denselben Kanal fälschlich erneut markieren.
        channel_label = rec.meta.get("channel_label") if rec.meta else None
        if channel_label and not seen_channel_labels.get(channel_label):
            _add_heading_with_bookmark(
                doc.text, 2, str(channel_label), style_names.get("H.Sub"),
                f"toc_bm_{next(bookmark_counter)}", toc_entries,
            )
            seen_channel_labels[channel_label] = True
        link_text = rec.meta.get("link") if rec.meta else None
        header_runs = rec.meta.get("header_runs") if rec.meta else None
        p_header = _build_header_paragraph(doc, header_runs, link_text, style_names)
        if p_header is not None:
            doc.text.addElement(p_header)
        # Jede Nachricht als Absatzblock (nutze Defaultstil)
        render_runs_into_container(doc, doc.text, rec.runs, style_names, style_names.get("P.Base"))
        separator_style = style_names.get("P.MessageSeparator")
        if separator_style:
            doc.text.addElement(P(stylename=separator_style))

    _populate_toc(toc_body, style_names, toc_entries)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path


_MISSING_TRANSLATION_PLACEHOLDER = "[Keine Übersetzung verfügbar]"


def write_odt_for_record_pairs(
    pairs: List[RecordPair],
    out_path: Path,
    styles: Dict[str, Any],
    doc_title: str | None = None,
    original_label: str = "Original",
    translation_label: str = "Übersetzung",
) -> Path:
    """side_by_side-Layout: eine Tabellenzeile pro Nachricht(-Paar), Original
    links / Übersetzung rechts. Bewusst eine Tabellenzeile pro Nachricht statt
    echtem ODT-Spalten-Layout (fo:column-count) - sonst bleiben Original und
    Übersetzung nicht zeilensynchron (Machbarkeitsentscheidung, siehe Feature-
    Beschreibung).

    Section-Überschriften (H1/H2) stehen bewusst AUSSERHALB jeder Tabelle
    (nicht als Tabellenzeile) - bei jedem chat-Wechsel wird die laufende
    Tabelle abgeschlossen, die Überschrift eingefügt und danach eine neue
    Tabelle begonnen. Das hält TOC/Gliederungsebenen exakt wie im linearen
    Layout (siehe write_odt_for_records) und verhindert, dass die Überschrift
    pro Zeile wiederholt wird.

    Fehlt eine Übersetzung (Provider-Fehler, Nachricht war schon in
    Zielsprache, ...), bleibt die Zelle nicht leer, sondern zeigt einen
    Platzhaltertext (siehe _MISSING_TRANSLATION_PLACEHOLDER) - konsistent mit
    dem bestehenden Muster für fehlende Inhalte in diesem Modul
    ("[CE:doc_id]", "[IMG missing: ...]"), statt eine erklärungslos leere
    Zelle oder eine über beide Spalten zusammengeführte Zeile zu erzeugen
    (Layout-Konsistenz: jede Zeile bleibt zweispaltig).
    """
    doc = OpenDocumentText()
    style_names = _ensure_min_styles(doc, styles or {})
    style_names = _ensure_table_styles(doc, style_names, usable_width_cm=_PAGE_USABLE_WIDTH_LANDSCAPE_CM)
    col_width_cm = style_names["_side_by_side_col_width_cm"]
    # Zellpolsterung beidseitig abziehen, nie unter eine sinnvolle Mindestbreite fallen.
    max_img_width_cm = max(col_width_cm - 0.4, 2.0)

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
    toc_body = _add_toc(doc, style_names)
    _add_footer(doc, style_names, landscape=True)

    current_chat = None
    seen_subheading: Dict[str, bool] = {}
    seen_channel_labels: Dict[str, bool] = {}
    current_table: Any = None
    table_idx = 0
    toc_entries: List[Tuple[str, int, str]] = []
    bookmark_counter = itertools.count(1)

    def _start_new_table() -> Any:
        nonlocal table_idx
        table_idx += 1
        t = Table(name=f"MessagesTable{table_idx}", stylename=style_names["Table.SideBySide"])
        t.addElement(TableColumn(stylename=style_names["TCol.Half"]))
        t.addElement(TableColumn(stylename=style_names["TCol.Half"]))
        header_row = TableRow()
        c1 = TableCell(stylename=style_names["TCell.ColumnHeader"])
        p1 = P(stylename=style_names.get("P.Base"))
        p1.addElement(Span(text=_sanitize_text(original_label), stylename=style_names.get("T.ColumnHeader")))
        c1.addElement(p1)
        c2 = TableCell(stylename=style_names["TCell.ColumnHeader"])
        p2 = P(stylename=style_names.get("P.Base"))
        p2.addElement(Span(text=_sanitize_text(translation_label), stylename=style_names.get("T.ColumnHeader")))
        c2.addElement(p2)
        header_row.addElement(c1)
        header_row.addElement(c2)
        t.addElement(header_row)
        return t

    for pair in pairs:
        rec = pair.original
        if rec.chat != current_chat:
            if current_table is not None:
                doc.text.addElement(current_table)
                current_table = None
            heading_style = style_names.get("H.Base") if current_chat is None else style_names.get("H.Break")
            _add_heading_with_bookmark(
                doc.text, 1, str(rec.chat), heading_style,
                f"toc_bm_{next(bookmark_counter)}", toc_entries,
            )
            current_chat = rec.chat
            if rec.meta and rec.meta.get("subheading") and not seen_subheading.get(rec.chat):
                _add_heading_with_bookmark(
                    doc.text, 2, str(rec.meta["subheading"]), style_names.get("H.Sub"),
                    f"toc_bm_{next(bookmark_counter)}", toc_entries,
                )
                seen_subheading[rec.chat] = True
        # channel_label (chronologisches Mischen): rec.chat bleibt über die
        # gesamte Tabelle gleich, daher unabhängig vom chat-Wechsel geprüft;
        # "erstes Auftreten" wird - wie in write_odt_for_records - erst hier
        # beim Schreiben der finalen Liste ermittelt (siehe Kommentar dort),
        # nicht beim Sammeln. Die laufende Tabelle wird davor abgeschlossen
        # (Überschriften stehen bewusst außerhalb jeder Tabelle, siehe
        # Docstring oben) und danach neu begonnen.
        channel_label = rec.meta.get("channel_label") if rec.meta else None
        if channel_label and not seen_channel_labels.get(channel_label):
            if current_table is not None:
                doc.text.addElement(current_table)
                current_table = None
            _add_heading_with_bookmark(
                doc.text, 2, str(channel_label), style_names.get("H.Sub"),
                f"toc_bm_{next(bookmark_counter)}", toc_entries,
            )
            seen_channel_labels[channel_label] = True
        if current_table is None:
            current_table = _start_new_table()

        row = TableRow()

        # Original-Spalte: Header (Zeitstempel/Link/Autor bzw. bei aktivem
        # Interleaving die "Titel: <Name>"-Zeile) + Nachrichtentext.
        cell_orig = TableCell(stylename=style_names["TCell.Base"])
        link_text = rec.meta.get("link") if rec.meta else None
        header_runs = rec.meta.get("header_runs") if rec.meta else None
        p_header = _build_header_paragraph(doc, header_runs, link_text, style_names)
        if p_header is not None:
            cell_orig.addElement(p_header)
        render_runs_into_container(doc, cell_orig, rec.runs, style_names, style_names.get("P.CellBase"), max_image_width_cm=max_img_width_cm)

        # Übersetzungs-Spalte: nur der übersetzte Text, kein eigener Header
        # (Zeitstempel/Kanal gehören zum Original, eine Wiederholung wäre in
        # der Seite-an-Seite-Ansicht redundant).
        cell_tr = TableCell(stylename=style_names["TCell.Base"])
        if pair.translation is not None:
            render_runs_into_container(doc, cell_tr, pair.translation.runs, style_names, style_names.get("P.CellBase"), max_image_width_cm=max_img_width_cm)
        else:
            p_missing = P(stylename=style_names.get("P.CellBase"))
            p_missing.addElement(Span(text=_MISSING_TRANSLATION_PLACEHOLDER))
            cell_tr.addElement(p_missing)

        row.addElement(cell_orig)
        row.addElement(cell_tr)
        current_table.addElement(row)

    if current_table is not None:
        doc.text.addElement(current_table)

    _populate_toc(toc_body, style_names, toc_entries)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out_path))
    return out_path
