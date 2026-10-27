"""Shared source identity and request-scoped catalog visibility."""
from sqlalchemy import event, or_, and_
from sqlalchemy.orm import Session, with_loader_criteria
from ..models import Product, Employee, Supplier, Location, Setting, PurchaseOrder


def matches(product, location):
    return bool(product and location and (product.source, product.source_account) == (location.source, location.source_account))


def require_match(db, location_id, product_id):
    if not matches(db.get(Product, product_id), db.get(Location, location_id)):
        raise ValueError('Choose a product from the same POS account as the store')


@event.listens_for(Session, 'do_orm_execute')
def scope_catalog(state):
    pairs = state.session.info.get('source_pairs')
    if not state.is_select or pairs is None: return
    for model in (Product, Employee, Supplier):
        criterion = or_(*[and_(model.source == source, model.source_account == account) for source, account in pairs]) if pairs else model.id == -1
        state.statement = state.statement.options(with_loader_criteria(model, criterion, include_aliases=True))
    ids = state.session.info.get('location_scope', [])
    criterion = PurchaseOrder.location_id.in_(ids or [-1])
    if state.session.info.get('allow_unassigned_pos'):
        criterion = or_(criterion, PurchaseOrder.location_id.is_(None))
    state.statement = state.statement.options(with_loader_criteria(PurchaseOrder, criterion, include_aliases=True))


def setup_scope(db, user, selection):
    from ..security import allowed_location_ids
    # Do not reapply an old request scope when calculating actual authorization.
    db.info.pop('location_scope', None)
    allowed = allowed_location_ids(db, user)
    from sqlalchemy import select
    locations = list(db.scalars(select(Location).where(Location.id.in_(allowed or [0]))))
    options = [('all', 'All POS sources'), ('storehub', 'StoreHub'), ('loyverse', 'Loyverse')]
    for account in sorted({x.source_account for x in locations if x.source == 'loyverse'}):
        label = db.get(Setting, 'loyverse.account_name.' + account)
        options.append(('loyverse:' + account, 'Loyverse / ' + (str(label.value) if label else account.replace('LOYVERSE_TOKEN_', ''))))
    if selection not in {x[0] for x in options}: selection = 'storehub'
    if selection == 'loyverse':  # the switch lists each Loyverse account on its own; an old combined choice opens the first account
        accounts = [x[0] for x in options if x[0].startswith('loyverse:')]
        if accounts: selection = accounts[0]
    chosen = [x for x in locations if selection == 'all' or x.source == selection or selection == x.source + ':' + x.source_account]
    db.info['location_scope'] = [x.id for x in chosen]
    pairs = {(x.source, x.source_account) for x in chosen}
    # Preserve legacy catalog access for installations without configured stores.
    if not locations and selection in ('storehub', 'all') and user.role in ('admin', 'management'): pairs.add(('storehub', ''))
    db.info['source_pairs'] = pairs
    db.info['allow_unassigned_pos'] = ('storehub', '') in pairs and not user.location_ids
    return selection, options
