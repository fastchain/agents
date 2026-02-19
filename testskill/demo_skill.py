#!/usr/bin/env python3
"""
Demo skill for Claude that showcases code generation capabilities
"""

def generate_hello_world(language="python"):
    """
    Generate a simple hello world program in the specified language
    """
    code_templates = {
        "python": '''
def hello_world():
    print("Hello, World!")

# Call the function
hello_world()
''',
        "javascript": '''
function helloWorld() {
    console.log("Hello, World!");
}

// Call the function
helloWorld();
''',
        "java": '''
public class HelloWorld {
    public static void main(String[] args) {
        System.out.println("Hello, World!");
    }
}
''',
        "c++": '''
#include <iostream>
int main() {
    std::cout << "Hello, World!" << std::endl;
    return 0;
}
'''
    }
    
    return code_templates.get(language, code_templates["python"])

def explain_code(code):
    """
    Explain the given code
    """
    explanations = {
        "python": "This is a Python function that prints 'Hello, World!' to the console. It defines a function called hello_world and then calls it.",
        "javascript": "This is a JavaScript function that prints 'Hello, World!' to the console. The function is defined with the function keyword and uses console.log() to output text.",
        "java": "This is a Java program with a main method. The main method is the entry point of the application, and it uses System.out.println() to print 'Hello, World!' to the console.",
        "c++": "This is a C++ program that includes the iostream library. The main function uses std::cout to print 'Hello, World!' to the console."
    }
    
    return explanations.get(code, "This code demonstrates a simple Hello World program.")

if __name__ == "__main__":
    print("Demo Claude Skill")
    print("Generating Hello World in Python:")
    print(generate_hello_world("python"))