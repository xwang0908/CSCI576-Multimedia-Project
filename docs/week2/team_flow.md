# Team Workflow

```mermaid
flowchart TD
    IN[input video]
    IN --> A[Person A<br/>video extraction]
    IN --> B[Person B<br/>audio extraction]
    A --> C[Person C<br/>integrator]
    B --> C
    C --> D[Person D<br/>frontend]
```
