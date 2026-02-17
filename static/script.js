const socket = io();

// Join poll room
socket.emit("join_poll", { poll_id: pollId });

// Handle vote submission
document.getElementById("voteForm").addEventListener("submit", function(e) {
    e.preventDefault();

    const selectedOption = document.querySelector('input[name="option"]:checked');
    const voteButton = document.getElementById("voteButton");

    if (!selectedOption) {
        return;
    }

    fetch(`/vote/${pollId}`, {
        method: "POST",
        headers: {
            "Content-Type": "application/json"
        },
        body: JSON.stringify({ option_id: selectedOption.value })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            voteButton.disabled = true;
            voteButton.innerText = "Vote Submitted ✓";
            voteButton.style.backgroundColor = "#16a34a";
        } else {
            alert(data.message);
        }
    });
});

// Real-time update listener
socket.on("update_results", function(results) {
    results.forEach(option => {
        const voteSpan = document.getElementById(`votes-${option.id}`);
        if (voteSpan) {
            voteSpan.innerText = option.vote_count;
        }
    });
});
