from adaptix._internal.provider.essential import DirectMediator, Request, RequestChecker


class AlwaysTrueRequestChecker(RequestChecker):  # noqa: PLW1641
    def check_request(self, mediator: DirectMediator, request: Request, /) -> bool:
        return True

    def __eq__(self, other):
        if isinstance(other, type(self)):
            return True
        return NotImplemented
