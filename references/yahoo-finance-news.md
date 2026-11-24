# Yahoo Finance News Source

The stock watcher uses Yahoo Finance as its **only news-discovery source**. It
searches the public Yahoo Finance search endpoint:

- Endpoint: <https://query1.finance.yahoo.com/v1/finance/search>
- Website: <https://finance.yahoo.com/>

For each selected daily price move, the script searches Yahoo Finance using the
ticker and move date, then the ticker alone. If no articles are found and a
company name is available, it retries with that name. Results within three
days of the move are preferred; if none match that window, the closest result
is used and identified as such.

Yahoo Finance's results may link to articles written by other publishers. The
watcher does not search those publishers or another news aggregator; it keeps
the publisher name and article URL supplied with Yahoo Finance's result for
attribution and reporting. Articles are associated with the price snapshot
for the move date. News timing or topic is presented as possible context, not
proof that an article caused a price change.

The implementation normalizes Yahoo Finance's current and legacy news result
shapes, including title, URL, publication time, publisher, summary, and related
ticker symbols. Network failures and empty results are reported distinctly.
