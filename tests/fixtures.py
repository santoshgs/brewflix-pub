"""Synthetic pages that mimic the live IMDb / Untappd markup the scrapers target.

They use the same selectors, attributes and "Show More" behaviour, so the
whole pipeline (Playwright pagination -> parsing -> merge -> JSON/CSV) can be
exercised offline. When a site changes its markup, save the real page with
`page.content()` into tests/fixtures/ and adjust parsers until tests pass.
"""
import json

IMDB_TITLES = [
    # const, title, year, type, imdb, mine, rated
    (f"tt{1000000 + i}", f"Film Number {i}", 1990 + i % 30, "Movie" if i % 4 else "TV Series",
     round(5 + (i % 50) / 10, 1), 1 + i % 10, f"2026-{1 + i % 10:02d}-{1 + i % 27:02d}")
    for i in range(1, 121)
]


def imdb_card(i, t):
    const, title, year, ttype, imdb, mine, rated = t
    type_span = f'<span class="sc-dli-title-metadata-item dli-title-metadata-item">{ttype}</span>' if ttype != "Movie" else ""
    month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(rated[5:7]) - 1]
    return f"""
<li class="ipc-metadata-list-summary-item">
  <div class="ipc-poster"><img class="ipc-image" src="https://m.media-amazon.com/images/M/{const}._V1_QL75_UX140_.jpg" alt="{title}"></div>
  <div class="ipc-title"><a href="/title/{const}/?ref_=rt_li_tt"><h3 class="ipc-title__text">{i}. {title}</h3></a></div>
  <div class="dli-title-metadata">
    <span class="dli-title-metadata-item">{year}</span>{type_span}
  </div>
  <span data-testid="ratingGroup--imdb-rating" class="ipc-rating-star--imdb"><span class="ipc-rating-star--rating">{imdb}</span></span>
  <span data-testid="ratingGroup--other-user-rating" class="ipc-rating-star--otherUserAlsoRated"><span class="ipc-rating-star--rating">{mine}</span></span>
  <div class="dli-user-list-item-description">Rated on {month} {int(rated[8:])}, {rated[:4]}</div>
</li>"""


def imdb_page(titles=IMDB_TITLES, page_size=50, late_ratings=False):
    # __NEXT_DATA__ carries only the first page, like the real site
    nd = {"props": {"pageProps": {"mainColumnData": {"advancedTitleSearch": {"edges": [
        {"node": {"title": {"id": c, "titleText": {"text": t}, "releaseYear": {"year": y},
                            "titleType": {"text": ty}, "primaryImage": {"url": f"https://m.media-amazon.com/images/M/{c}._V1_.jpg"}, "titleGenres": {"genres": [{"genre": {"text": "Drama"}}, {"genre": {"text": "Comedy"}}]}, "ratingsSummary": {"aggregateRating": r}},
                  "userRating": {"value": m, "date": d + "T12:00:00Z"}}}
        for c, t, y, ty, r, m, d in titles[:page_size]]}}}}}
    cards = [imdb_card(i + 1, t) for i, t in enumerate(titles)]
    late = ""
    if late_ratings:  # like the live site: first paint has no rating/date, they arrive ~1.5s later
        import re as _re
        first = cards[:page_size]
        hidden = [_re.sub(r'<span data-testid="ratingGroup--other-user-rating".*?</div>', "", c, flags=_re.S) for c in first]
        late = ("<script>setTimeout(()=>{const d=document.createElement('ul');d.innerHTML=document.getElementById('full').textContent;"
                "document.getElementById('list').replaceChildren(...d.children);},1500)</script>"
                f'<script type="text/html" id="full">{"".join(first)}</script>')
        cards = hidden + cards[page_size:]
        nd = {"props": {"pageProps": {"mainColumnData": {"advancedTitleSearch": {"edges": [
            {"node": {"title": {"id": c, "titleText": {"text": t}}}} for c, t, *_ in titles[:page_size]]}}}}}
    return f"""<!doctype html><html><head><title>Your Ratings • IMDb</title>
<script id="__NEXT_DATA__" type="application/json">{json.dumps(nd)}</script></head><body>
<ul id="list">{''.join(cards[:page_size])}</ul>
<span class="ipc-see-more"><button class="ipc-see-more__button">50 more</button></span>
<script type="text/html" id="rest">{''.join(cards[page_size:])}</script>{late}
<script>
const tmp=document.createElement('ul'); tmp.innerHTML=document.getElementById('rest').textContent; const rest=[...tmp.children]; let shown=0;
document.querySelector('.ipc-see-more__button').onclick=()=>setTimeout(()=>{{
  rest.slice(shown,shown+{page_size}).forEach(n=>document.getElementById('list').appendChild(n.cloneNode(true)));
  shown+={page_size}; if(shown>=rest.length) document.querySelector('.ipc-see-more').remove();
}},300);
</script></body></html>"""


UNTAPPD_CHECKINS = [
    dict(id=str(900000 - i), bid=str(5000 + i % 70), beer=f"Hoppy Thing {i % 70}",
         brewery=f"Brewery {i % 15}", brewery_slug=f"Brewery{i % 15}", venue=None if i % 3 else f"Pub {i % 5}",
         rating=None if i % 11 == 0 else round(2.5 + (i % 10) / 4, 2),
         comment="Nice & juicy" if i % 4 == 0 else "", serving=["Draft", "Can", "Bottle", ""][i % 4],
         photo=f"https://images.untp.beer/crop?w=640&h=640&url=photos/{i}.jpg" if i % 5 == 0 else None,
         when=f"Sat, {1 + i % 28:02d} {['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct'][i % 10]} 2026 19:{i % 60:02d}:00 +0000")
    for i in range(130)
]


def untappd_item(c):
    venue = f' at <a href="/v/{c["venue"].lower().replace(" ", "-")}/77{c["id"][-2:]}">{c["venue"]}</a>' if c["venue"] else ""
    caps = f'<div class="caps " data-rating="{c["rating"]}"><div class="cap cap-100"></div></div>' if c["rating"] is not None else ""
    serving = f'<p class="serving"><img><span>{c["serving"]}</span></p>' if c["serving"] else ""
    photo = f'<p class="photo"><a href="/user/demo-drinker/checkin/{c["id"]}/photo"><img class="lazy" data-original="{c["photo"]}" src="/blank.gif"></a></p>' if c["photo"] else ""
    return f"""
<div class="item" id="checkin_{c['id']}" data-checkin-id="{c['id']}">
 <div class="checkin"><div class="top">
  <a href="/b/x-y/{c['bid']}" class="label"><img src="https://assets.untappd.com/site/beer_logos/beer-{c['bid']}.jpeg"></a>
  <p class="text"><a href="/user/demo-drinker" class="user">Demo</a> is drinking a <a href="/b/x-y/{c['bid']}">{c['beer']}</a> by <a href="/{c['brewery_slug']}">{c['brewery']}</a>{venue}</p>
  <div class="checkin-comment"><p class="comment-text" id="translate_{c['id']}">{c['comment']}</p>
   <div class="rating-serving">{serving}{caps}</div></div>
  {photo}
 </div>
 <div class="feedback"><div class="bottom"><a class="time timezoner track-click" data-gregtime="{c['when']}" href="/user/demo-drinker/checkin/{c['id']}">x</a></div></div>
 </div>
</div>"""


def _more_script(btn_sel, container, page_size, cap):
    return f"""<script>
const tmp=document.createElement('div'); tmp.innerHTML=document.getElementById('rest').textContent; const rest=[...tmp.children]; let shown=0, clicks=0;
document.querySelector('{btn_sel}').addEventListener('click',e=>{{e.preventDefault(); clicks++;
  if({cap} && clicks>{cap}) return;  // simulate the logged-out pagination cap
  setTimeout(()=>{{rest.slice(shown,shown+{page_size}).forEach(n=>document.getElementById('{container}').appendChild(n.cloneNode(true)));
  shown+={page_size}; if(shown>=rest.length) document.querySelector('{btn_sel}').remove();}},250);}});
</script>"""


def untappd_profile(checkins=UNTAPPD_CHECKINS, page_size=25, cap=0):
    items = [untappd_item(c) for c in checkins]
    return f"""<!doctype html><html><head><title>Demo on Untappd</title></head><body>
<div id="main-stream">{''.join(items[:page_size])}</div>
<a class="yellow button more_checkins more-feed" href="javascript:void(0);">Show More</a>
<script type="text/html" id="rest">{''.join(items[page_size:])}</script>
{_more_script('a.more_checkins', 'main-stream', page_size, cap)}</body></html>"""


def untappd_beers_page(checkins=UNTAPPD_CHECKINS, page_size=25):
    beers = {}
    for c in checkins:
        b = beers.setdefault(c["bid"], {**c, "n": 0, "dates": []})
        b["n"] += 1
    items = []
    for bid, b in beers.items():
        abv = 4 + int(bid) % 6 + 0.5
        items.append(f"""
<div class="beer-item " data-bid="{bid}">
 <a class="label" href="/b/x-y/{bid}"><img src="https://assets.untappd.com/site/beer_logos/beer-{bid}.jpeg"></a>
 <div class="beer-details">
  <p class="name"><a href="/b/x-y/{bid}">{b['beer']}</a></p>
  <p class="brewery"><a href="/{b['brewery_slug']}">{b['brewery']}</a></p>
  <p class="style">{['IPA - New England / Hazy','Stout - Imperial','Lager - Helles','Sour - Gose'][int(bid) % 4]}</p>
  <div class="ratings">
   <div class="you"><p>Their Rating ({b['rating']})</p><div class="caps" data-rating="{b['rating'] or 0}"></div></div>
   <div class="you"><p>Global Rating (3.81)</p><div class="caps" data-rating="3.81"></div></div>
  </div>
 </div>
 <div class="details beer">
  <p class="abv">{abv}% ABV</p><p class="ibu">{'N/A' if int(bid) % 5 == 0 else str(int(bid) % 80)} IBU</p>
  <p class="check-ins">Total: {b['n']}</p>
  <p class="date">First: <a><abbr class="date-time">10/01/25</abbr></a></p>
  <p class="date">Recent: <a><abbr class="date-time">11/01/26</abbr></a></p>
 </div>
</div>""")
    return f"""<!doctype html><html><head><title>Demo's Beers</title></head><body>
<div class="distinct-list-list-container" id="beers">{''.join(items[:page_size])}</div>
<a class="yellow button more_beers more-list-items" href="javascript:void(0);">Show More</a>
<script type="text/html" id="rest">{''.join(items[page_size:])}</script>
{_more_script('a.more_beers', 'beers', page_size, 0)}</body></html>"""
