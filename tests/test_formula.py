"""Formula tokeniser and parser tests.

Two properties matter. Everything inside the grammar parses into the right
shape, and everything outside it raises rather than degrading into something
that looks plausible. The second is the one that protects every impact figure
downstream, so the rejection tests are as detailed as the acceptance ones.
"""

import pytest

from materia.formula import (
    BinaryOp,
    Boolean,
    CellRef,
    FunctionCall,
    Number,
    Percent,
    RangeRef,
    Text,
    UnaryOp,
    UnsupportedFormula,
    parse_formula,
    references,
    tokenise,
    walk,
)
from materia.parse import Reference


def kinds(formula: str) -> list[str]:
    return [token.kind for token in tokenise(formula)]


class TestOperators:
    """Every arithmetic and comparison operator in the grammar."""

    @pytest.mark.parametrize("operator", ["+", "-", "*", "/", "^"])
    def test_arithmetic(self, operator):
        node = parse_formula(f"=A1{operator}B1")
        assert isinstance(node, BinaryOp)
        assert node.operator == operator

    @pytest.mark.parametrize("operator", ["=", "<>", "<", "<=", ">", ">="])
    def test_comparison(self, operator):
        node = parse_formula(f"=A1{operator}B1")
        assert isinstance(node, BinaryOp)
        assert node.operator == operator

    def test_unary_minus(self):
        assert parse_formula("=-A1") == UnaryOp("-", CellRef(Reference(1, 1)))

    def test_unary_plus(self):
        assert parse_formula("=+A1") == UnaryOp("+", CellRef(Reference(1, 1)))

    def test_repeated_unary_minus(self):
        assert parse_formula("=--A1") == UnaryOp("-", UnaryOp("-", CellRef(Reference(1, 1))))

    def test_percent(self):
        assert parse_formula("=10%") == Percent(Number(10.0))

    def test_percent_applies_to_a_reference(self):
        assert parse_formula("=A1%") == Percent(CellRef(Reference(1, 1)))


class TestPrecedence:
    """Excel's precedence, which is not the same as Python's.

    Two of these would be wrong if the parser were written the familiar way,
    and both would be silent: a plausible number, computed incorrectly.
    """

    def test_multiplication_binds_tighter_than_addition(self):
        node = parse_formula("=A1+A2*A3")
        assert node.operator == "+"
        assert node.right.operator == "*"

    def test_parentheses_override_precedence(self):
        node = parse_formula("=(A1+A2)*A3")
        assert node.operator == "*"
        assert node.left.operator == "+"

    def test_unary_minus_binds_tighter_than_exponent(self):
        """Excel gives -2^2 = 4. Python gives -4."""
        node = parse_formula("=-2^2")
        assert node.operator == "^"
        assert node.left == UnaryOp("-", Number(2.0))

    def test_exponent_is_left_associative(self):
        """Excel gives 2^3^2 = 64. Python gives 512."""
        node = parse_formula("=2^3^2")
        assert node.operator == "^"
        assert node.left == BinaryOp("^", Number(2.0), Number(3.0))
        assert node.right == Number(2.0)

    def test_exponent_binds_tighter_than_multiplication(self):
        node = parse_formula("=A1*A2^A3")
        assert node.operator == "*"
        assert node.right.operator == "^"

    def test_comparison_is_loosest(self):
        node = parse_formula("=A1+A2>A3*A4")
        assert node.operator == ">"
        assert node.left.operator == "+"
        assert node.right.operator == "*"

    def test_subtraction_is_left_associative(self):
        """10-3-2 is 5, not 9."""
        node = parse_formula("=10-3-2")
        assert node.left == BinaryOp("-", Number(10.0), Number(3.0))
        assert node.right == Number(2.0)

    def test_percent_binds_tighter_than_multiplication(self):
        node = parse_formula("=A1*50%")
        assert node.operator == "*"
        assert node.right == Percent(Number(50.0))


class TestFunctions:
    """Every function in the grammar, plus nesting and argument counts."""

    @pytest.mark.parametrize(
        "formula,name,argument_count",
        [
            ("=SUM(A1:A3)", "SUM", 1),
            ("=SUM(A1,A2,A3)", "SUM", 3),
            ("=AVERAGE(A1:A3)", "AVERAGE", 1),
            ("=MIN(A1:A3)", "MIN", 1),
            ("=MAX(A1:A3)", "MAX", 1),
            ("=IF(A1>0,1,2)", "IF", 3),
            ("=IF(A1>0,1)", "IF", 2),
            ("=ROUND(A1,2)", "ROUND", 2),
            ("=ABS(A1)", "ABS", 1),
            ('=SUMIF(A1:A3,">2")', "SUMIF", 2),
            ('=SUMIF(A1:A3,">2",B1:B3)', "SUMIF", 3),
        ],
    )
    def test_each_supported_function(self, formula, name, argument_count):
        node = parse_formula(formula)
        assert isinstance(node, FunctionCall)
        assert node.name == name
        assert len(node.arguments) == argument_count

    def test_nested_calls(self):
        node = parse_formula("=ROUND(AVERAGE(A1:A3),2)")
        assert node.name == "ROUND"
        assert node.arguments[0].name == "AVERAGE"

    def test_deeply_nested_calls(self):
        node = parse_formula("=IF(SUM(A1:A3)>MAX(B1:B3),ROUND(ABS(C1),2),0)")
        names = [found.name for found in walk(node) if isinstance(found, FunctionCall)]
        assert names == ["IF", "SUM", "MAX", "ROUND", "ABS"]

    def test_expressions_as_arguments(self):
        node = parse_formula("=SUM(A1*2,B1+3)")
        assert node.arguments[0].operator == "*"
        assert node.arguments[1].operator == "+"

    def test_function_name_is_case_insensitive(self):
        assert parse_formula("=sum(A1:A3)") == parse_formula("=SUM(A1:A3)")


class TestReferences:
    def test_relative(self):
        assert parse_formula("=A1") == CellRef(Reference(1, 1))

    def test_absolute(self):
        assert parse_formula("=$A$1") == CellRef(
            Reference(1, 1, absolute_column=True, absolute_row=True)
        )

    def test_mixed_locked_column(self):
        assert parse_formula("=$A1") == CellRef(Reference(1, 1, absolute_column=True))

    def test_mixed_locked_row(self):
        assert parse_formula("=A$1") == CellRef(Reference(1, 1, absolute_row=True))

    def test_range(self):
        node = parse_formula("=SUM(A1:B3)")
        reference = node.arguments[0]
        assert isinstance(reference, RangeRef)
        assert (reference.start.column, reference.start.row) == (1, 1)
        assert (reference.end.column, reference.end.row) == (2, 3)

    def test_cross_sheet_cell(self):
        assert parse_formula("=Assumptions!B2").reference.sheet == "Assumptions"

    def test_cross_sheet_range_carries_the_sheet_to_both_ends(self):
        """Excel writes Sheet1!A1:A3, not Sheet1!A1:Sheet1!A3.

        The end of the range has to inherit the qualifier, or the graph will
        look for those cells on the wrong sheet.
        """
        node = parse_formula("=SUM(Assumptions!A1:A3)")
        reference = node.arguments[0]
        assert reference.start.sheet == "Assumptions"
        assert reference.end.sheet == "Assumptions"

    def test_quoted_sheet_name(self):
        assert parse_formula("='My Sheet'!B2").reference.sheet == "'My Sheet'"

    def test_walk_descends_through_unary_and_percent(self):
        """walk has a branch per node shape, so each shape needs visiting."""
        node = parse_formula("=SUM(-A1,B1%)")
        visited = [type(found).__name__ for found in walk(node)]
        assert "UnaryOp" in visited
        assert "Percent" in visited
        assert visited.count("CellRef") == 2

    def test_references_helper_reaches_inside_unary_and_percent(self):
        found = list(references(parse_formula("=-A1+B1%")))
        assert len(found) == 2

    def test_references_helper_collects_every_reference(self):
        node = parse_formula("=SUM(A1:A3)+B1*Assumptions!C1")
        found = list(references(node))
        assert len(found) == 3
        assert isinstance(found[0], RangeRef)


class TestLiterals:
    def test_integer(self):
        assert parse_formula("=5") == Number(5.0)

    def test_decimal(self):
        assert parse_formula("=1.5") == Number(1.5)

    def test_leading_decimal_point(self):
        assert parse_formula("=.5") == Number(0.5)

    def test_scientific_notation(self):
        """1E5 puts a letter next to digits, where a reference could hide."""
        assert parse_formula("=1E5") == Number(100000.0)

    def test_text(self):
        assert parse_formula('="hello"') == Text("hello")

    def test_text_with_an_escaped_quote(self):
        assert parse_formula('="say ""hi"""') == Text('say "hi"')

    def test_text_containing_a_paren_is_not_a_call(self):
        assert parse_formula('="up (good)"') == Text("up (good)")

    @pytest.mark.parametrize("text,value", [("TRUE", True), ("FALSE", False)])
    def test_boolean(self, text, value):
        assert parse_formula(f"={text}") == Boolean(value)


class TestRejection:
    """Anything outside the grammar raises. Nothing degrades or guesses."""

    def test_an_unsupported_function_raises_rather_than_returning_something(self):
        with pytest.raises(UnsupportedFormula) as raised:
            parse_formula("=VLOOKUP(A1,A1:B3,2,FALSE)")
        assert "VLOOKUP" in str(raised.value)

    @pytest.mark.parametrize(
        "name", ["VLOOKUP", "INDEX", "MATCH", "NPV", "XIRR", "SQRT", "CONCATENATE"]
    )
    def test_functions_outside_the_grammar(self, name):
        with pytest.raises(UnsupportedFormula):
            parse_formula(f"={name}(A1)")

    def test_a_function_name_that_is_also_a_valid_reference(self):
        """LOG10 parses as column LOG row 10. The paren makes it a call."""
        with pytest.raises(UnsupportedFormula) as raised:
            parse_formula("=LOG10(A1)")
        assert "LOG10" in str(raised.value)

    def test_a_dynamic_array_function(self):
        with pytest.raises(UnsupportedFormula):
            parse_formula("=_xlfn.UNIQUE(A1:A3)")

    def test_string_concatenation_operator(self):
        with pytest.raises(UnsupportedFormula):
            parse_formula('=A1&"x"')

    def test_a_defined_name(self):
        """A bare identifier is not a reference, a function or a literal."""
        with pytest.raises(UnsupportedFormula) as raised:
            parse_formula("=Tax_Rate*2")
        assert "Tax_Rate" in str(raised.value)

    @pytest.mark.parametrize(
        "formula",
        [
            "=SUM(A1:A3",  # unbalanced
            "=SUM A1:A3)",
            "=A1+",  # dangling operator
            "=*A1",
            "=A1 A1",  # two values with nothing between them
            "=",  # empty
            "=()",
            "=A1,B1",  # comma outside a call
        ],
    )
    def test_malformed_formulas(self, formula):
        with pytest.raises(UnsupportedFormula):
            parse_formula(formula)

    @pytest.mark.parametrize(
        "formula",
        [
            "=SUM()",
            "=ABS(A1,A2)",
            "=ROUND(A1)",
            "=ROUND(A1,2,3)",
            "=IF(A1>0)",
            "=IF(A1>0,1,2,3)",
            "=SUMIF(A1:A3)",
        ],
    )
    def test_wrong_argument_counts(self, formula):
        """Arity is checked here so the recompute engine never has to guess."""
        with pytest.raises(UnsupportedFormula):
            parse_formula(formula)

    def test_a_missing_comma_between_arguments(self):
        """Reports what was found and where, not just that parsing failed."""
        with pytest.raises(UnsupportedFormula) as raised:
            parse_formula("=SUM(A1 A2)")
        assert "rparen" in str(raised.value).lower()

    def test_the_error_names_the_formula(self):
        with pytest.raises(UnsupportedFormula) as raised:
            parse_formula("=VLOOKUP(A1,A1:B3,2,FALSE)")
        assert "VLOOKUP(A1,A1:B3,2,FALSE)" in str(raised.value)


class TestTokeniser:
    def test_skips_whitespace(self):
        assert kinds("= A1 + B1 ") == ["REFERENCE", "OPERATOR", "REFERENCE"]

    def test_a_formula_without_a_leading_equals(self):
        assert kinds("A1+B1") == kinds("=A1+B1")

    def test_two_character_operators_are_one_token(self):
        assert kinds("=A1<>B1") == ["REFERENCE", "OPERATOR", "REFERENCE"]
        assert tokenise("=A1<=B1")[1].text == "<="

    def test_a_range_is_a_single_token(self):
        assert kinds("=SUM(A1:A3)") == [
            "FUNCTION", "LPAREN", "REFERENCE", "RPAREN",
        ]

    def test_rejects_an_unknown_character(self):
        with pytest.raises(UnsupportedFormula) as raised:
            tokenise("=A1 @ B1")
        assert "@" in str(raised.value)


@pytest.mark.parametrize("name", ["clean", "copied_formulas", "deep_chain"])
def test_every_formula_in_an_accepted_workbook_parses(workbooks, name):
    """Preflight accepting a workbook has to mean the parser can read it.

    If these ever disagree, the pipeline accepts a file and then fails partway
    through, which is the outcome the preflight stage exists to avoid.
    """
    from materia.parse import read_formulas
    from materia.preflight import preflight

    preflight(workbooks[name])
    formulas = read_formulas(workbooks[name])
    assert formulas
    for cell in formulas:
        parse_formula(cell.formula)


def test_the_parser_and_preflight_agree_on_the_function_list():
    """One grammar, one list. Drift here would mean preflight accepts a
    formula the parser cannot read, or rejects one it can."""
    from materia.formula import SUPPORTED_FUNCTIONS
    from materia.preflight import SUPPORTED_FUNCTIONS as preflight_functions

    assert SUPPORTED_FUNCTIONS is preflight_functions
