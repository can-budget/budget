import Foundation

struct GoldenCase: Decodable {
    let name: String
    let input: BudgetInput
    let expected: Projection
}
@main struct Verify {
    static func main() throws {
        guard CommandLine.arguments.count == 2 else {
            fputs("Usage: verify golden_cases.json\n", stderr)
            exit(2)
        }
        let file = URL(fileURLWithPath: CommandLine.arguments[1])
        let cases = try JSONDecoder().decode([GoldenCase].self, from: Data(contentsOf: file))
        for test in cases {
            let actual = try BudgetEngine.project(test.input)
            guard actual == test.expected else {
                fputs("FAILED: \(test.name)\nExpected: \(test.expected)\nActual: \(actual)\n", stderr)
                exit(1)
            }
        }
        print("Swift: \(cases.count) cross-language golden cases passed")
    }
}
