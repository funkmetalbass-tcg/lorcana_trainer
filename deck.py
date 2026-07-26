class Deck:
    ''' Each player's deck list and deck/discard pile'''
    def __init__(self, card_list: list[dict[str, any]]):
        self.master_list = [Card(**data) for data in card_list]
        self.deck = []
        self.discard = []
        self.shuffle()

    def shuffle(self):
        self.deck = self.master_list.copy()
        random.shuffle(self.deck)

    def draw_card(self) -> Card | None:
        if not self.deck:
            return None # Lose if deck is empty
        return self.deck.pop(0)
