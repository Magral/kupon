#!/usr/bin/env python3
"""Collect public Lenta/Yarche delivery promotions into a dated, source-attributed feed."""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

SOURCES = [
    ("lenta", "official", "Лента · официальные акции", "https://lenta.com/action/"),
    ("yarche", "official", "Ярче! · официальный каталог", "https://yarcheplus.ru/"),
    ("lenta", "community", "Pepper · сообщество скидок", "https://www.pepper.ru/coupons/lenta"),
    ("yarche", "community", "Pepper · сообщество скидок", "https://www.pepper.ru/coupons/yarcheplus.ru"),
    ("lenta", "editorial", "Picodi", "https://www.picodi.com/ru/lenta"),
    ("lenta", "editorial", "ПромоКодекс", "https://promocodex.ru/promocodes/lenta/"),
    ("yarche", "editorial", "Promokodi.net", "https://promokodi.net/store/yarche/"),
    ("yarche", "editorial", "ПромКод.ру", "https://www.promkod.ru/shop/yarcheplus"),
    ("yarche", "editorial", "Промокодoff", "https://promokodoff.ru/promokody-yarche/"),
    ("yarche", "community", "Pikadil · предложения сообщества", "https://pikadil.ru/view/yarcheplus.ru"),
    ("lenta", "telegram", "Telegram · публичная лента промокодов", "https://t.me/s/lenta_kodi"),
]
HEADERS = {"User-Agent": "KuponOfferCollector/0.2 (+https://github.com/Magral/kupon)"}
PROMO_WORDS = re.compile(r"промокод|купон|скидк|акци|бесплатн.{0,20}достав|эконом", re.I)
CODE = re.compile(r"(?:промокод|код)\s*[:№\-]?\s*[`'«\"]?([A-ZА-ЯЁ0-9][A-ZА-ЯЁ0-9_-]{3,19})", re.I)
COMPETITORS = re.compile(r"яндекс\s*лавк|пят[её]рочк|перекр[её]сток|магнит|самокат|ашан|окей|вкусвилл|купер", re.I)
ORDER_SCOPE = re.compile(r"заказ|корзин|самовывоз|доставк", re.I)
PRODUCT_SCOPE = re.compile(r"молоч|мяс|птиц|овощ|фрукт|сыр|снек|товар|продукт|каталог", re.I)
PERCENT = re.compile(r"(?:скидк\w*\s*(?:до\s*)?|[-−]\s*)(\d{1,2})\s*%", re.I)
FREE_SHIP = re.compile(r"бесплатн.{0,25}достав|достав.{0,20}бесплатн", re.I)
RUBLES = re.compile(r"(?:скидк\w*\s*(?:до\s*)?|[-−]\s*)(\d{2,5})\s*(?:₽|руб(?:\.|лей|ля|ль)?)", re.I)
MINIMUM = re.compile(r"(?:от|заказ\s+от)\s*(\d{3,6})\s*(?:₽|руб(?:\.|лей|ля|ль)?)", re.I)
DATE_NUMERIC = re.compile(r"(?:до|действует\s+до|по)\s*(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?", re.I)
MONTHS = {"января":1,"февраля":2,"марта":3,"апреля":4,"мая":5,"июня":6,"июля":7,"августа":8,"сентября":9,"октября":10,"ноября":11,"декабря":12}
DATE_RU = re.compile(r"(?:до|по)\s*(\d{1,2})\s+(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)(?:\s+(20\d{2}))?", re.I)


def expiry(text: str) -> str | None:
    m = DATE_NUMERIC.search(text)
    if m:
        day, month, year = m.group(1, 2, 3)
        y = int(year) if year else date.today().year
        if y < 100: y += 2000
        try:
            parsed = date(y, int(month), int(day))
            return parsed.isoformat() if parsed >= date.today() else None
        except ValueError: return None
    m = DATE_RU.search(text.lower())
    if m:
        day, month, year = m.group(1), MONTHS[m.group(2)], int(m.group(3) or date.today().year)
        try:
            parsed = date(year, month, int(day))
            return parsed.isoformat() if parsed >= date.today() else None
        except ValueError: return None
    return None


def relevant(store: str, title: str, context: str, link: str, source_page: str, kind: str, source_name: str) -> bool:
    target = re.compile(r"ярче|yarche", re.I) if store == "yarche" else re.compile(r"лента|lenta", re.I)
    title_has = bool(target.search(title))
    combined = title + " " + context
    if "Picodi" in source_name and not re.search(r"/(?:online-)?lenta(?:/|$)", urlparse(link).path, re.I):
        return False
    if "Pikadil" in source_name and not re.search(r"/view/yarcheplus\.ru", urlparse(link).path, re.I):
        return False
    if kind == "telegram" and COMPETITORS.search(title + " " + context):
        return False
    if COMPETITORS.search(title) and not title_has:
        return False
    page_path = urlparse(source_page).path.lower()
    page_is_specific = bool(re.search(r"lenta|yarche", page_path))
    return bool(target.search(combined)) or page_is_specific


def offer_scope(text: str, offer_type: str | None) -> str:
    if offer_type == "delivery": return "delivery"
    if ORDER_SCOPE.search(text): return "basket"
    if PRODUCT_SCOPE.search(text): return "product"
    return "unknown"


def candidate_telegram(soup: BeautifulSoup, base_url: str):
    for wrap in soup.select(".tgme_widget_message_wrap"):
        body = wrap.select_one(".tgme_widget_message_text")
        if body is None: continue
        text = " ".join(body.get_text(" ", strip=True).split())
        if not text or not PROMO_WORDS.search(text): continue
        post = wrap.get("data-post", "")
        link = "https://t.me/" + post if post else base_url
        yield text[:180], text[:900], link


def source_grade(kind: str, text: str) -> tuple[str, str]:
    if kind == "official":
        return "official", "Опубликовано магазином; условия и адрес доставки нужно сверить"
    if kind == "community":
        return "community", "Предложение сообщества; проверьте код в корзине"
    if re.search(r"проверено|работает|проверен редакцией|применен\s+\d+\s+раз", text, re.I):
        return "editor_checked", "Источник сообщает о проверке; повторно проверьте в корзине"
    return "unverified", "Неподтверждённая публикация; проверьте код в корзине"


def candidate_blocks(soup: BeautifulSoup, base_url: str):
    seen = set()
    for a in soup.select("a[href]"):
        href = a.get("href", "").strip()
        title = " ".join(a.get_text(" ", strip=True).split())
        if not href or not title or href.startswith(("#", "javascript:", "mailto:")):
            continue
        node = a
        best = title
        for _ in range(4):
            node = node.parent
            if not getattr(node, "get_text", None): break
            text = " ".join(node.get_text(" ", strip=True).split())
            if 45 <= len(text) <= 900 and PROMO_WORDS.search(text):
                best = text
                break
        if not PROMO_WORDS.search(best): continue
        absolute = urljoin(base_url, href)
        key = (title.lower(), absolute)
        if key in seen: continue
        seen.add(key)
        yield title[:180], best[:900], absolute


def collect():
    now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    records, errors = [], []
    session = requests.Session(); session.headers.update(HEADERS)
    for store, kind, label, source_url in SOURCES:
        try:
            response = session.get(source_url, timeout=25, allow_redirects=True)
            response.raise_for_status()
            if "text/html" not in response.headers.get("content-type", "").lower():
                raise ValueError("non-HTML response")
            soup = BeautifulSoup(response.text, "html.parser")
            source_count = 0
            candidates = candidate_telegram(soup, response.url) if kind == "telegram" else candidate_blocks(soup, response.url)
            for title, context, link in candidates:
                if source_count >= 8:
                    break
                # Keep external references inside the source page domain; this prevents unrelated ads.
                if urlparse(link).netloc and urlparse(link).netloc != urlparse(response.url).netloc:
                    continue
                combined = context + " " + title
                if not relevant(store, title, context, link, response.url, kind, label):
                    continue
                date_evidence = combined + " " + link
                if re.search(r"нерабоч|не\s+работает|не\s+действ|ист[её]к|заверш[её]н|закончилась|акция\s+окончена", combined, re.I):
                    continue
                date_text = DATE_NUMERIC.search(date_evidence) or DATE_RU.search(date_evidence.lower())
                expires = expiry(date_evidence)
                if date_text and expires is None:
                    continue
                code_match = CODE.search(combined)
                code_value = code_match.group(1) if code_match else None
                if code_match and re.match(r"\s*[%₽]", combined[code_match.end():]):
                    code_value = None
                if code_value and code_value.casefold() in {"скопировано", "показать", "открыть", "купон"}:
                    code_value = None
                discount = PERCENT.search(combined)
                free_ship = FREE_SHIP.search(combined)
                rubles = RUBLES.search(combined)
                minimum = MINIMUM.search(combined)
                kind_value = "percent" if discount else "fixed" if rubles else "delivery" if free_ship else None
                scope = offer_scope(combined, kind_value)
                grade, note = source_grade(kind, combined)
                headline = context[:150] if title.casefold() in {"подробнее", "открыть", "открыть код", "показать код", "перейти", "акция", "купон", "view in telegram", "скопировано"} else title if len(title) >= 8 else context[:150]
                records.append({
                    "store": store,
                    "title": headline,
                    "description": context[:420],
                    "code": code_value,
                    "type": kind_value,
                    "scope": scope,
                    "value": int(discount.group(1)) if discount else int(rubles.group(1)) if rubles else 0 if free_ship else None,
                    "minimum": int(minimum.group(1)) if minimum else 0,
                    "expires": expires,
                    "source_name": label,
                    "source_url": link,
                    "source_page": response.url,
                    "source_kind": kind,
                    "verification": grade,
                    "verification_note": note,
                    "updated_at": now,
                })
                source_count += 1
        except Exception as exc:
            errors.append({"source": label, "url": source_url, "error": str(exc)[:180]})
        time.sleep(0.4)
    # Merge multiple links from one promotion card and keep a compact mobile feed.
    merged = {}
    for item in records:
        key = (item["store"], item["source_url"])
        if key not in merged:
            merged[key] = item
            continue
        old = merged[key]
        if len(item["title"]) > len(old["title"]) and not old["type"]:
            old["title"] = item["title"]
        if not old["code"] and item["code"]: old["code"] = item["code"]
        if not old["type"] and item["type"]:
            old["type"], old["value"], old["scope"] = item["type"], item["value"], item["scope"]
        if not old["expires"] and item["expires"]: old["expires"] = item["expires"]
        old["minimum"] = max(old["minimum"], item["minimum"])
    deduped = list(merged.values())
    return {"schema_version":1,"updated_at":now,"stores":["lenta","yarche"],"count":len(deduped),"offers":deduped[:80],"source_errors":errors}


if __name__ == "__main__":
    data = collect()
    with open("offers.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"Collected {data['count']} offer links; {len(data['source_errors'])} sources failed")
    if data["count"] == 0 and data["source_errors"]:
        raise SystemExit("All sources failed; preserving the last known feed")
