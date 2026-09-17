"""
Shared output shape for all language parsers. Every parser (python_parser.py,
js_parser.py, and any future one) produces these same dataclasses so the
rest of the pipeline (graph_builder.py) never needs to know which language
a file was written in.
"""
from dataclasses import dataclass, field


@dataclass
class ImportBinding:
    """One name bound into a file's scope by an import/require statement."""
    local_name: str          # the identifier as used in this file's code
    module: str                                                                        
    orig_name: str | None = None                                                                         
                                                                            


@dataclass
class ExtractedSymbol:
    name: str                                                 
    qualified_name: str                                                                 
    type: str                                                 
    start_line: int
    end_line: int
    parent_qualified_name: str | None = None
    calls: list[str] = field(default_factory=list)                                          
    is_test: bool = False
    local_bindings: set[str] = field(default_factory=set)
                                                                           
                                                                            
                                                                           
                                                                   
                                                                          
    typed_calls: list[tuple[str, str]] = field(default_factory=list)
                                                                           
                                                                            
                                                                            
                                                                 
                                                         
                                                                  
                                                                         
                                                                      
                                                                         
                                                                          
                                                                          
                                  


@dataclass
class ExtractedFile:
    relpath: str
    language: str
    imports: list[ImportBinding] = field(default_factory=list)
    symbols: list[ExtractedSymbol] = field(default_factory=list)
