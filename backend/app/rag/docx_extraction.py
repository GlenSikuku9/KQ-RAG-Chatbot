from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from lxml import etree
from lxml.etree import _Element


@dataclass
class ExtractionResult:
    text: str
    warnings: list[str] = field(default_factory=list)


class DocxExtractor:
    """Preserve Word structure without inferring semantic table headers."""

    def __init__(self, numbering: _Element | None = None) -> None:
        self.warnings: list[str] = []
        self.has_text = False
        self.numbering = numbering
        self.list_counts: dict[tuple[str, int], int] = {}

    def warn(self, location: str, message: str) -> None:
        warning = f"{location}: {message}"
        if warning not in self.warnings:
            self.warnings.append(warning)

    def blocks(self, parent: _Element, location: str) -> str:
        output: list[str] = []
        for index, child in enumerate(parent, start=1):
            current = f"{location}/block {index}"
            if child.tag == qn("w:p"):
                text = self.paragraph(child, current)
            elif child.tag == qn("w:tbl"):
                text = self.table(child, current)
            elif child.tag in {qn("w:sdt"), qn("w:sdtContent"), qn("w:customXml")}:
                self.warn(current, "Content control or custom XML requires review.")
                text = self.blocks(child, current)
            elif child.tag in {qn("w:ins"), qn("w:del"), qn("w:moveFrom"), qn("w:moveTo")}:
                self.warn(current, "Tracked changes must be accepted or rejected in Word.")
                text = self.blocks(child, current)
            elif child.tag in {qn("w:sectPr"), qn("w:tcPr"), qn("w:sdtPr"), qn("w:sdtEndPr")}:
                continue
            else:
                if child.tag == qn("w:altChunk") or list(child.iter(qn("w:t"))):
                    self.warn(current, "Unsupported block content requires conversion to editable paragraphs.")
                continue
            if text.strip():
                output.append(text.strip())
        return "\n\n".join(output)

    def paragraph(self, paragraph: _Element, location: str) -> str:
        prefix = self.list_prefix(paragraph, location)
        return prefix + self.inline(paragraph, location).strip()

    def list_prefix(self, paragraph: _Element, location: str) -> str:
        properties = paragraph.find("./" + qn("w:pPr") + "/" + qn("w:numPr"))
        if properties is None:
            return ""
        num_id = properties.find(qn("w:numId"))
        level = properties.find(qn("w:ilvl"))
        key = num_id.get(qn("w:val")) if num_id is not None else None
        level_id = level.get(qn("w:val"), "0") if level is not None else "0"
        if key == "0":
            return ""
        level_index = int(level_id)
        for count_key in list(self.list_counts):
            if count_key[0] == key and count_key[1] > level_index:
                del self.list_counts[count_key]
        number = next((item for item in self.numbering if item.tag == qn("w:num")
                       and item.get(qn("w:numId")) == key), None) if self.numbering is not None else None
        abstract_id = number.find(qn("w:abstractNumId")) if number is not None else None
        abstract = next((item for item in self.numbering if item.tag == qn("w:abstractNum")
                         and item.get(qn("w:abstractNumId")) == abstract_id.get(qn("w:val"))), None
                        ) if self.numbering is not None and abstract_id is not None else None
        definition = next((item for item in abstract if item.tag == qn("w:lvl")
                           and item.get(qn("w:ilvl")) == level_id), None) if abstract is not None else None
        if definition is not None and number is not None and abstract is not None and key is not None:
            fmt = definition.find(qn("w:numFmt"))
            template = definition.find(qn("w:lvlText"))
            format_name = fmt.get(qn("w:val")) if fmt is not None else None
            overrides = number.findall(qn("w:lvlOverride"))
            if format_name == "bullet" and not overrides:
                return f"[List level {level_index + 1}] - "
            placeholder = f"%{level_index + 1}"
            if (format_name in {"decimal", "lowerLetter", "upperLetter"} and 0 <= level_index <= 8
                    and template is not None
                    and template.get(qn("w:val")) in {placeholder + ".", placeholder + ")"}
                    and not overrides and not list(abstract.iter(qn("w:lvlRestart")))
                    and definition.find(qn("w:isLgl")) is None):
                start = definition.find(qn("w:start"))
                initial = int(start.get(qn("w:val"), "1")) if start is not None else 1
                if format_name == "decimal" or initial > 0:
                    count_key = (key, level_index)
                    count = self.list_counts.get(count_key, initial - 1) + 1
                    self.list_counts[count_key] = count
                    label = str(count)
                    if format_name in {"lowerLetter", "upperLetter"}:
                        base = ord("a" if format_name == "lowerLetter" else "A")
                        # Word repeats letters after z: aa, bb, ..., zz, aaa.
                        label = chr(base + (count - 1) % 26) * ((count - 1) // 26 + 1)
                    nesting = f"[List level {level_index + 1}] " if level_index else ""
                    return nesting + template.get(qn("w:val"), "").replace(placeholder, label) + " "
        self.warn(location, "Unsupported automatic list labels require review; paragraph text is preserved.")
        return "[List item] "

    def inline(self, element: _Element, location: str) -> str:
        if etree.QName(element).localname in {"oMath", "oMathPara", "AlternateContent"}:
            self.warn(location, "Equation or alternate visual content requires review.")
        if element.tag in {qn("w:t"), qn("w:delText")}:
            text = element.text or ""
            self.has_text = self.has_text or bool(text.strip())
            return text
        if element.tag in {qn("w:pPr"), qn("w:rPr")}:
            return ""
        if element.tag == qn("w:tab"):
            return "\t"
        if element.tag in {qn("w:br"), qn("w:cr")}:
            return "\n"
        if element.tag == qn("w:noBreakHyphen"):
            return "-"
        if element.tag == qn("w:softHyphen"):
            return ""
        if element.tag == qn("w:txbxContent"):
            self.warn(location, "Text box extracted inline; verify its reading order and associations.")
            return "\nText box:\n" + self.blocks(element, location + "/text box") + "\n"
        if element.tag in {qn("w:drawing"), qn("w:pict"), qn("w:object")}:
            self.warn(location, "Drawing, image, or embedded object requires visual review; no OCR is performed.")
        if element.tag in {qn("w:ins"), qn("w:del"), qn("w:moveFrom"), qn("w:moveTo")}:
            self.warn(location, "Tracked changes must be accepted or rejected in Word.")
        if element.tag in {qn("w:fldChar"), qn("w:instrText"), qn("w:fldSimple")}:
            self.warn(location, "Dynamic field requires review of its displayed value.")
            if element.tag == qn("w:instrText"):
                return ""
        if element.tag == qn("w:sym"):
            self.warn(location, "Font-encoded symbol requires conversion to editable text.")
        if element.tag in {qn("w:footnoteReference"), qn("w:endnoteReference")}:
            kind = "Footnote" if element.tag == qn("w:footnoteReference") else "Endnote"
            return f" [{kind} {element.get(qn('w:id'), '?')}]"
        if element.tag in {qn("w:commentReference"), qn("w:commentRangeStart")}:
            self.warn(location, "Document comments require review.")
        return "".join(self.inline(child, location) for child in element)

    def table(self, table: _Element, location: str) -> str:
        grid = table.find(qn("w:tblGrid"))
        width = len(grid) if grid is not None else 0
        if not width:
            self.warn(location, "Table has no explicit column grid.")
        lines = ["Table:"]
        # Column ranges map continuations to their original merged cell text.
        previous_merges: dict[tuple[int, int], tuple[int, str]] = {}
        for row_number, row in enumerate(table.findall(qn("w:tr")), start=1):
            row_location = f"{location}/row {row_number}"
            properties = row.find(qn("w:trPr"))
            before = self.grid_value(properties, "gridBefore", 0, row_location)
            after = self.grid_value(properties, "gridAfter", 0, row_location)
            column = before + 1
            cells = [f"Column {i}: [omitted]" for i in range(1, column)]
            current_merges: dict[tuple[int, int], tuple[int, str]] = {}
            if any(child.tag not in {qn("w:trPr"), qn("w:tc"), qn("w:tblPrEx")} for child in row):
                self.warn(row_location, "Wrapped or revised table cells require review.")
            for cell in row.findall(qn("w:tc")):
                cell_properties = cell.find(qn("w:tcPr"))
                span = self.grid_value(cell_properties, "gridSpan", 1, row_location)
                if span < 1:
                    self.warn(row_location, "Invalid column span.")
                    span = 1
                end = column + span - 1
                label = f"Column {column}" if span == 1 else f"Columns {column}-{end}"
                text = self.blocks(cell, f"{row_location}/{label}") or "[empty]"
                if cell_properties is not None and cell_properties.find(qn("w:hMerge")) is not None:
                    self.warn(row_location, "Legacy horizontal merge requires conversion or review.")
                merge = cell_properties.find(qn("w:vMerge")) if cell_properties is not None else None
                key = (column, end)
                if merge is not None:
                    mode = merge.get(qn("w:val"), "continue")
                    if mode == "restart":
                        current_merges[key] = (row_number, text)
                    elif mode == "continue" and key in previous_merges:
                        origin, original_text = previous_merges[key]
                        current_merges[key] = (origin, original_text)
                        if text != "[empty]":
                            self.warn(row_location, "Vertical merge continuation contains additional content.")
                            original_text += "\n" + text
                        text = f"[merged from row {origin}] {original_text}"
                    else:
                        self.warn(row_location, "Unmatched or invalid vertical merge.")
                cells.append(f"{label}: {text.replace(chr(10), chr(10) + '    ')}")
                column = end + 1
            cells.extend(f"Column {i}: [omitted]" for i in range(column, column + after))
            if width and column - 1 + after != width:
                self.warn(row_location, "Row width does not match the table grid.")
            header = properties.find(qn("w:tblHeader")) if properties is not None else None
            is_header = header is not None and header.get(qn("w:val"), "true") not in {"0", "false", "off"}
            suffix = " (declared header)" if is_header else ""
            lines.append(f"Row {row_number}{suffix}: " + " | ".join(cells))
            previous_merges = current_merges
        if any(child.tag not in {qn("w:tblPr"), qn("w:tblGrid"), qn("w:tr")} for child in table):
            self.warn(location, "Wrapped or revised table rows require review.")
        return "\n".join(lines)

    def grid_value(self, properties: _Element | None, name: str, default: int, location: str) -> int:
        element = properties.find(qn(f"w:{name}")) if properties is not None else None
        if element is None:
            return default
        try:
            value = int(element.get(qn("w:val"), ""))
            if value < 0:
                raise ValueError
            return value
        except ValueError:
            self.warn(location, f"Invalid {name} value; table requires review.")
            return default


def extract_docx(file_path: Path) -> ExtractionResult:
    document = Document(file_path)
    numbering_part = next((part for part in document.part.package.parts
                           if str(part.partname) == "/word/numbering.xml"), None)
    numbering = etree.fromstring(numbering_part.blob, parser=etree.XMLParser(
        resolve_entities=False, no_network=True
    )) if numbering_part is not None else None
    extractor = DocxExtractor(numbering)
    for tag in ("ins", "del", "moveFrom", "moveTo", "pPrChange", "rPrChange", "tblPrChange", "tcPrChange"):
        if list(document.element.iter(qn(f"w:{tag}"))):
            extractor.warn("Body", "Tracked content or formatting changes require review.")
    if list(document.element.iter(qn("w:vanish"))):
        extractor.warn("Body", "Hidden text requires review of what should be published.")
    blocks = [extractor.blocks(document.element.body, "Body")]
    note_ids: dict[str, set[str]] = {"footnotes.xml": set(), "endnotes.xml": set()}
    # Read only existing parts, avoiding creation of empty header/footer definitions.
    for part in sorted(document.part.package.parts, key=lambda item: str(item.partname)):
        name = str(part.partname)
        if name.startswith(("/word/header", "/word/footer")) and name.endswith(".xml"):
            root = etree.fromstring(part.blob, parser=etree.XMLParser(resolve_entities=False, no_network=True))
            content = extractor.blocks(root, name)
            if content:
                blocks.append(f"Supplementary content ({name}):\n{content}")
        elif name in {"/word/footnotes.xml", "/word/endnotes.xml"}:
            root = etree.fromstring(part.blob, parser=etree.XMLParser(resolve_entities=False, no_network=True))
            kind = "Footnote" if "footnotes" in name else "Endnote"
            for note in root:
                if note.get(qn("w:type")) in {"separator", "continuationSeparator", "continuationNotice"}:
                    continue
                note_id = note.get(qn("w:id"), "?")
                note_ids[name.rsplit("/", 1)[-1]].add(note_id)
                content = extractor.blocks(note, f"{kind} {note_id}")
                if content:
                    blocks.append(f"{kind} {note_id}:\n{content}")
    for reference_tag, part_name in (("footnoteReference", "footnotes.xml"), ("endnoteReference", "endnotes.xml")):
        for reference in document.element.iter(qn(f"w:{reference_tag}")):
            if reference.get(qn("w:id"), "?") not in note_ids[part_name]:
                extractor.warn("Body", f"Referenced note in {part_name} is missing.")
    # Style-based numbering cannot be recovered from paragraph text alone.
    used_styles = {item.get(qn("w:val")) for item in document.element.iter(qn("w:pStyle"))}
    for style in document.styles:
        if style.style_id in used_styles and list(style.element.iter(qn("w:numPr"))):
            extractor.warn("Body", "Style-based automatic list labels require review.")
    text = "\n\n".join(block for block in blocks if block.strip()) if extractor.has_text else ""
    return ExtractionResult(text=text, warnings=extractor.warnings)
