# Rea

## liga_cardsearch

Varredura do catálogo de cartas Pokémon usando o autocomplete da LigaPokemon:
`GET https://www.clubedaliga.com.br/api/cardsearch?tcg=2&maxQuantity=10&maintype=1&query=...`

- Endpoint não documentado; limite fixo de 10 resultados por busca; sem autenticação.
- `python3 liga_cardsearch/scrape.py --out data` (retomável; cache em `data/queries.jsonl`).
- `liga_cardsearch/run.sh` roda a varredura e faz commit/push do progresso periodicamente.
- Saída: `data/cards.json` e `data/cards.csv` (key, liga_card_id, liga_edition_id, number,
  set_total, name_pt, name_en, image).
- Cobertura: numerações `NNN/TTT` e `NNNJP/TTT`. Promos com códigos (ex.: `XY-P`, `SWSH`)
  podem ficar incompletas. Preços não estão neste endpoint.
