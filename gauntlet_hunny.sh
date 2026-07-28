python3 run.py \
--deck-a ./my_decks/deck-A_blurple_hunny_v2.txt \
--deck-b ./opponent_decks/deck-set_13_damage_discard.txt \
gauntlet \
--a mcts --b mcts \
--games 100 \
--out ./analysis-hunny/gauntlet_hunny \
--workers 6 \
--field ./opponent_decks/* \
